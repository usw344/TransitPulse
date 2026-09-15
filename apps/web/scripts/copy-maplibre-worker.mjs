/**
 * Copy MapLibre's tile-parsing worker into `public/` so it is served as JavaScript.
 *
 * MapLibre 6 ships the worker as a separate ES module and locates it with
 * `new URL("./maplibre-gl-worker.mjs", import.meta.url)`.  After Turbopack
 * bundles the library, `import.meta.url` points into `/_next/static/chunks/`,
 * where that file does not exist — Next answers with its HTML 404 page, the
 * browser refuses the module for its `text/html` MIME type, and the worker
 * never starts.  Raster tiles still render (they decode on the main thread)
 * but every vector tile silently fails to parse, so no vector source ever
 * finishes loading and `map.on("load")` never fires.
 *
 * Copying the worker plus the shared chunk it imports to a stable same-origin
 * path lets `setWorkerUrl()` point at a real file.  Run before dev and build so
 * the copy tracks the installed version instead of drifting from it.
 */
import { copyFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const distDir = dirname(require.resolve("maplibre-gl/dist/maplibre-gl.mjs"));
const outDir = join(process.cwd(), "public", "maplibre");

// The worker imports the shared chunk by relative path, so both must land here.
const FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];

await mkdir(outDir, { recursive: true });
for (const file of FILES) {
  await copyFile(join(distDir, file), join(outDir, file));
}
console.log(`maplibre worker assets copied to public/maplibre (${FILES.join(", ")})`);
