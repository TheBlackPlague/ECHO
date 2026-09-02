import {defineConfig} from "@hey-api/openapi-ts";


export default defineConfig({
    input: "../generated/openapi.json",
    output: "./src/api/generated",
});
