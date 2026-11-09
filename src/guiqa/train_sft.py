import argparse
import hashlib
import json
from pathlib import Path

import accelerate
import peft
import torch
import transformers
import yaml
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import (AutoProcessor, Qwen3VLForConditionalGeneration, Trainer,
                          TrainerCallback, TrainingArguments, set_seed)

from .artifacts import write_json
from .sft_collator import QwenSftCollator


def main():
    parser = argparse.ArgumentParser(description="Qwen3-VL language LoRA SFT")
    parser.add_argument("--config", type=Path, default=Path("configs/sft-train.yaml"))
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--stop-after", type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(args.config.read_text())
    dataset = root / config["dataset"]
    output = root / config["output_dir"]
    model_path = root / config["model_path"]
    torch.set_num_threads(4)
    set_seed(config["seed"])
    training_args = TrainingArguments(
        output_dir=str(output), num_train_epochs=config["epochs"],
        per_device_train_batch_size=config["per_device_batch_size"],
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=config["gradient_accumulation_steps"],
        learning_rate=config["learning_rate"], weight_decay=config["weight_decay"],
        warmup_ratio=config["warmup_ratio"], lr_scheduler_type="cosine",
        bf16=True, tf32=True, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="adamw_torch", logging_steps=1,
        eval_strategy=config.get("eval_strategy", "no"),
        save_strategy=config.get("save_strategy", "steps"),
        save_steps=config.get("save_steps", 500),
        save_total_limit=config.get("save_total_limit", 2),
        load_best_model_at_end=config.get("load_best_model_at_end", False),
        metric_for_best_model=config.get("metric_for_best_model"),
        greater_is_better=config.get("greater_is_better"),
        report_to="none", remove_unused_columns=False,
        ddp_find_unused_parameters=False, prediction_loss_only=True,
        dataloader_num_workers=config["dataloader_num_workers"],
        seed=config["seed"], data_seed=config["seed"], disable_tqdm=True)
    processor = AutoProcessor.from_pretrained(
        model_path, local_files_only=True,
        min_pixels=config["min_pixels"], max_pixels=config["max_pixels"])
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        model_path, local_files_only=True, dtype=torch.bfloat16,
        attn_implementation="sdpa")
    model.config.use_cache = False
    model.enable_input_require_grads()
    model, init_adapter = initialize_language_adapter(model, config, root)
    train_data = [json.loads(line) for line in (dataset / "train.jsonl").read_text().splitlines()]
    validation_data = [json.loads(line) for line in (dataset / "validation.jsonl").read_text().splitlines()]
    trainer = AssistantTrainer(
        model=model, args=training_args, train_dataset=train_data, eval_dataset=validation_data,
        data_collator=QwenSftCollator(processor, dataset), processing_class=processor,
        callbacks=[StopAtCheckpoint(args.stop_after)] if args.stop_after else [])
    trainer.model_accepts_loss_kwargs = False
    if trainer.is_world_process_zero():
        output.mkdir(parents=True, exist_ok=True)
        model.print_trainable_parameters()
        if not args.resume_from:
            model_source = yaml.safe_load((root / "configs/sources.yaml").read_text())["model"]
            manifest = {
                "config": config, "world_size": training_args.world_size,
                "init_adapter": init_adapter,
                "effective_batch_size": (training_args.world_size * config["per_device_batch_size"]
                                         * config["gradient_accumulation_steps"]),
                "base_model": {"repo": model_source["repo"],
                               "revision": config.get("model_revision", model_source["revision"]),
                               "path": str(model_path),
                               "config_sha256": hashlib.sha256((model_path / "config.json").read_bytes()).hexdigest(),
                               "weights_index_sha256": hashlib.sha256(
                                   (model_path / "model.safetensors.index.json").read_bytes()).hexdigest()},
                "train_samples": len(train_data), "validation_samples": len(validation_data),
                "validation_case_ids": sorted({row["case_id"] for row in validation_data}),
                "data_sha256": {name: hashlib.sha256((dataset / name).read_bytes()).hexdigest()
                                for name in ("train.jsonl", "validation.jsonl", "manifest.json")},
                "versions": {"torch": torch.__version__, "transformers": transformers.__version__,
                             "peft": peft.__version__, "accelerate": accelerate.__version__},
                "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
                "vision_trainable_parameters": sum(p.numel() for p in model.base_model.model.model.visual.parameters()
                                                   if p.requires_grad),
                "loss": "assistant JSON and end-of-turn only; no truncation",
                "activation_storage": "torch.autograd.graph.save_on_cpu(pin_memory=True)",
            }
            write_json(output / "training_manifest.json", manifest)
    result = trainer.train(resume_from_checkpoint=str(args.resume_from) if args.resume_from else None)
    if trainer.state.global_step == trainer.state.max_steps:
        export_training_result(trainer, result.metrics, output)


def export_training_result(trainer, train_metrics, output):
    # Trainer.train() restores the selected PEFT adapter on every DDP rank before returning.
    trainer.save_model(str(output / "adapter"))
    trainer.save_state()
    trainer.save_metrics("train", train_metrics)
    if trainer.args.load_best_model_at_end:
        history = [{"epoch": row["epoch"], "step": row["step"], "eval_loss": row["eval_loss"],
                    "checkpoint": str(output / f"checkpoint-{row['step']}")}
                   for row in trainer.state.log_history if "eval_loss" in row]
        best_checkpoint = trainer.state.best_model_checkpoint
        best_step = int(Path(best_checkpoint).name.removeprefix("checkpoint-"))
        selected = next(row for row in history if row["step"] == best_step)
        validation_metrics = {"eval_loss": selected["eval_loss"], "epoch": selected["epoch"]}
        selected_epoch = selected["epoch"]
    else:
        validation_metrics = trainer.evaluate()
        history = [{"epoch": trainer.state.epoch, "step": trainer.state.global_step,
                    "eval_loss": validation_metrics["eval_loss"]}]
        best_checkpoint = None
        selected_epoch = trainer.state.epoch
    trainer.save_metrics("validation", validation_metrics)
    if trainer.is_world_process_zero():
        manifest = json.loads((output / "training_manifest.json").read_text())
        init_adapter = manifest.get("init_adapter")
        if init_adapter is not None and adapter_source(Path(init_adapter["path"])) != init_adapter:
            raise ValueError("initial adapter files changed during SFT")
        completion = {"completed": True, "optimizer_steps": trainer.state.global_step,
                      "epoch": trainer.state.epoch, "train_metrics": train_metrics,
                      "validation_metrics": validation_metrics, "epoch_validation": history,
                      "best_checkpoint": best_checkpoint, "selected_epoch": selected_epoch,
                      "selection_metric": trainer.args.metric_for_best_model,
                      "adapter": str(output / "adapter"),
                      "adapter_sha256": hashlib.sha256(
                          (output / "adapter/adapter_model.safetensors").read_bytes()).hexdigest(),
                      "init_adapter": init_adapter,
                      "init_adapter_bytes_preserved": True if init_adapter is not None else None,
                      **{key: manifest[key] for key in ("base_model", "data_sha256", "world_size",
                         "effective_batch_size", "train_samples", "validation_samples",
                         "validation_case_ids", "activation_storage", "trainable_parameters",
                         "vision_trainable_parameters")}}
        write_json(output / "completion.json", completion)
        print(json.dumps(completion), flush=True)


def initialize_language_adapter(model, config, root):
    if config.get("init_adapter_path"):
        path = (root / config["init_adapter_path"]).resolve()
        source = adapter_source(path)
        return PeftModel.from_pretrained(model, path, is_trainable=True), source
    model = get_peft_model(model, LoraConfig(
        r=config["lora_rank"], lora_alpha=config["lora_alpha"],
        lora_dropout=config["lora_dropout"], bias="none", task_type="CAUSAL_LM",
        target_modules=r"model\.language_model\.layers\.\d+\.(self_attn\.(q|k|v|o)_proj|mlp\.(gate|up|down)_proj)"))
    return model, None


def adapter_source(path):
    return {"path": str(path), "sha256": {
        name: hashlib.sha256((path / name).read_bytes()).hexdigest()
        for name in ("adapter_config.json", "adapter_model.safetensors")}}


class AssistantTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        labels = inputs.pop("labels")
        first_label = (labels != -100).any(dim=0).nonzero()[0, 0].item()
        keep = labels.shape[1] - first_label + 1
        # Predict the first answer token from the preceding prompt token; avoid prompt vocabulary logits.
        with torch.autograd.graph.save_on_cpu(pin_memory=True):
            outputs = model(**inputs, labels=labels[:, -keep:], logits_to_keep=keep)
        return (outputs.loss, outputs) if return_outputs else outputs.loss


class StopAtCheckpoint(TrainerCallback):
    def __init__(self, step):
        self.step = step

    def on_step_end(self, args, state, control, **kwargs):
        if state.global_step == self.step:
            control.should_save = True
            control.should_training_stop = True
        return control


if __name__ == "__main__":
    main()
