import { mkdir, stat, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const files = [
  ["https://hf-mirror.com/Xenova/multilingual-e5-small/resolve/main/config.json", "resources/models/multilingual-e5-small/config.json"],
  ["https://hf-mirror.com/Xenova/multilingual-e5-small/resolve/main/tokenizer.json", "resources/models/multilingual-e5-small/tokenizer.json"],
  ["https://hf-mirror.com/Xenova/multilingual-e5-small/resolve/main/tokenizer_config.json", "resources/models/multilingual-e5-small/tokenizer_config.json"],
  ["https://hf-mirror.com/Xenova/multilingual-e5-small/resolve/main/special_tokens_map.json", "resources/models/multilingual-e5-small/special_tokens_map.json"],
  ["https://hf-mirror.com/Xenova/multilingual-e5-small/resolve/main/onnx/model_quantized.onnx", "resources/models/multilingual-e5-small/onnx/model_quantized.onnx"],
  ["https://cdn.jsdelivr.net/npm/@tesseract.js-data/chi_sim/4.0.0_best_int/chi_sim.traineddata.gz", "resources/ocr/chi_sim.traineddata.gz"],
  ["https://cdn.jsdelivr.net/npm/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz", "resources/ocr/eng.traineddata.gz"],
];

for (const [url, destination] of files) {
  try { if ((await stat(destination)).size > 0) continue; } catch { /* download missing file */ }
  process.stdout.write(`Downloading ${destination}\n`);
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, Buffer.from(await response.arrayBuffer()));
}
