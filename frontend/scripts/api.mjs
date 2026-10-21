import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import openapiTS, { astToString } from 'openapi-typescript';
const schema = JSON.parse(await readFile(new URL('../../contracts/openapi.json', import.meta.url), 'utf8'));
const generated = astToString(await openapiTS(schema));
const destination = new URL('../src/api/generated.ts', import.meta.url);
if (process.argv.includes('--check')) {
  const directory = await mkdtemp(join(tmpdir(), 'lzo-api-check-'));
  try {
    const temporary = join(directory, 'generated.ts');
    await writeFile(temporary, generated);
    if (!(await readFile(temporary)).equals(await readFile(destination))) throw new Error('Generated API types differ; run api:generate');
  } finally { await rm(directory, { recursive: true }); }
} else { await writeFile(destination, generated); }
