import {cpSync, mkdirSync} from "node:fs";
import {join} from "node:path";

// PDF.js loads these assets at runtime from the static Nginx image; bundle them
// locally so rendering does not depend on a CDN or external network.
const destination = "dist/pdfjs";
mkdirSync(destination, {recursive: true});
for (const directory of ["cmaps", "standard_fonts", "wasm"]) {
  cpSync(join("node_modules/pdfjs-dist", directory), join(destination, directory), {recursive: true});
}
