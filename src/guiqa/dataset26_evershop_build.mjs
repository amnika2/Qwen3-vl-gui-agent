import { rmSync } from 'fs';
import config from 'config';
import { loadModuleRoutes } from './packages/evershop/dist/lib/router/loadModuleRoutes.js';
import { getRoutes } from './packages/evershop/dist/lib/router/Router.js';
import { lockHooks } from './packages/evershop/dist/lib/util/hookable.js';
import { lockRegistry } from './packages/evershop/dist/lib/util/registry.js';
import { validateConfiguration } from './packages/evershop/dist/lib/util/validateConfiguration.js';
import { getRouteBuildPath } from './packages/evershop/dist/lib/webpack/getRouteBuildPath.js';
import { getEnabledExtensions } from './packages/evershop/dist/bin/extension/index.js';
import { loadBootstrapScript } from './packages/evershop/dist/bin/lib/bootstrap/bootstrap.js';
import { buildEntry } from './packages/evershop/dist/bin/lib/buildEntry.js';
import { getCoreModules } from './packages/evershop/dist/bin/lib/loadModules.js';
import { compile } from './packages/evershop/dist/bin/build/complie.js';
import './packages/evershop/dist/bin/build/initEnvBuild.js';

const modules = [...getCoreModules(), ...getEnabledExtensions()];
for (const module of modules) {
  loadModuleRoutes(module.path);
}

for (const module of modules) {
  await loadBootstrapScript(module, {
    command: 'build',
    env: 'production',
    process: 'main'
  });
}
lockHooks();
lockRegistry();
validateConfiguration(config);
process.env.ALLOW_CONFIG_MUTATIONS = false;

const routes = getRoutes().filter((route) => route.id === 'productEdit');
for (const route of routes) {
  // Other pages retain their prebuilt routes and shared chunks.
  rmSync(getRouteBuildPath(route), { recursive: true });
}
await buildEntry(routes);
const stats = await compile(routes);
console.log(stats.toString({ colors: false, chunks: false, modules: false }));
