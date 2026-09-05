import sharp from "sharp";

const source = "design/reference-knowledge-classification.png";
const implementation = "design/implementation-knowledge.png";
const width = 720;
const normalizedSource = "design/reference-knowledge-classification-1440x1024.png";
const normalizedImplementation = "design/implementation-knowledge-1440x1024.png";
await sharp(source).resize(1440, 1024, { fit: "fill" }).png().toFile(normalizedSource);
const implementationMetaRaw = await sharp(implementation).metadata();
await sharp(implementation)
  .extend({ bottom: Math.max(0, 1024 - (implementationMetaRaw.height ?? 1024)), background: "#f8f7f3" })
  .resize(1440, 1024, { fit: "fill" })
  .png()
  .toFile(normalizedImplementation);
const sourceBuffer = await sharp(normalizedSource).resize({ width }).png().toBuffer();
const implementationBuffer = await sharp(normalizedImplementation).resize({ width }).png().toBuffer();
const sourceMeta = await sharp(sourceBuffer).metadata();
const implementationMeta = await sharp(implementationBuffer).metadata();
const height = Math.max(sourceMeta.height ?? 512, implementationMeta.height ?? 512);
await sharp({ create: { width: width * 2 + 24, height: height + 44, channels: 4, background: "#e9e7e2" } })
  .composite([
    { input: sourceBuffer, left: 0, top: 44 },
    { input: implementationBuffer, left: width + 24, top: 44 },
    { input: Buffer.from(`<svg width="${width * 2 + 24}" height="44"><rect width="100%" height="44" fill="#292b36"/><text x="18" y="28" fill="white" font-family="Arial" font-size="16">SOURCE · final visual target</text><text x="${width + 42}" y="28" fill="white" font-family="Arial" font-size="16">IMPLEMENTATION · 1440×1024</text></svg>`), left: 0, top: 0 },
  ])
  .png()
  .toFile("design/qa-comparison.png");

const focus = { left: 850, top: 78, width: 590, height: 930 };
const sourceDetail = await sharp(normalizedSource).extract(focus).png().toBuffer();
const implementationDetail = await sharp(normalizedImplementation).extract(focus).png().toBuffer();
await sharp({ create: { width: focus.width * 2 + 24, height: focus.height + 44, channels: 4, background: "#e9e7e2" } })
  .composite([
    { input: sourceDetail, left: 0, top: 44 },
    { input: implementationDetail, left: focus.width + 24, top: 44 },
    { input: Buffer.from(`<svg width="${focus.width * 2 + 24}" height="44"><rect width="100%" height="44" fill="#292b36"/><text x="18" y="28" fill="white" font-family="Arial" font-size="16">SOURCE · detail crop</text><text x="${focus.width + 42}" y="28" fill="white" font-family="Arial" font-size="16">IMPLEMENTATION · detail crop</text></svg>`), left: 0, top: 0 },
  ])
  .png()
  .toFile("design/qa-detail-comparison.png");
