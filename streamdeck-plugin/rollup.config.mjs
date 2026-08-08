import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";

export default {
  input: "src/plugin.ts",
  output: {
    file: "com.jacobchoi.jacky-control.sdPlugin/bin/plugin.js",
    format: "es",
    sourcemap: false,
    // The manifest's CodePath is a single file, so the lazy import of
    // ./audio-devices must be inlined rather than emitted as a chunk.
    inlineDynamicImports: true,
  },
  plugins: [
    typescript({ noEmit: false, declaration: false }),
    nodeResolve({ preferBuiltins: true, exportConditions: ["node"] }),
    commonjs(),
  ],
};
