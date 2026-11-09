"""Binary task rewards and bounded whole-group resampling for the short retry."""

import json

from .train_review_grpo import ReviewReward


def binary_training_reward(score):
    if score["reward"] is None:
        return None
    return 1.0 if score["strict_correct"] else -1.0


def group_eligible(rewards):
    return (all(value is not None for value in rewards)
            and any(value > 0 for value in rewards) and any(value < 0 for value in rewards))


class BinaryReviewReward(ReviewReward):
    __name__ = "verified_binary_review"

    def score_answers(self, sample, raw, completion_ids):
        scores, resolutions = super().score_answers(sample, raw, completion_ids)
        for score, resolution in zip(scores, resolutions):
            resolution["reward"] = binary_training_reward(score)
        return scores, resolutions

    def preview(self, sample_id, completion_ids, processor):
        sample = self.samples[sample_id]
        raw = processor.batch_decode(completion_ids, skip_special_tokens=True)
        scores, resolutions = self.score_answers(sample, raw, completion_ids)
        rewards = [row["reward"] for row in resolutions]
        return {"sample_id": sample_id, "label": sample["gold"]["verdict"], "raw": raw,
                "scores": scores, "binary_rewards": rewards, "eligible": group_eligible(rewards)}


def retry_trainer(reward, max_attempts):
    from .train_review_grpo_pilot import pilot_trainer

    class RetryTrainer(pilot_trainer()):
        def _generate_and_score_completions(self, inputs):
            self.retry_sample_id = inputs[0]["sample_id"]
            return super()._generate_and_score_completions(inputs)

        def _generate_single_turn(self, prompts):
            for attempt in range(max_attempts):
                generated = super()._generate_single_turn(prompts)
                preview = reward.preview(self.retry_sample_id, generated[1], self.processing_class)
                with (reward.output / "sampling-attempts.jsonl").open("a") as handle:
                    handle.write(json.dumps({"group": len(reward.groups), "attempt": attempt + 1,
                        "selected": preview["eligible"] or attempt + 1 == max_attempts,
                        **preview}, ensure_ascii=False) + "\n")
                if preview["eligible"]:
                    break
            return generated

    return RetryTrainer
