import { it, expect } from "vitest";
import { publicationState } from "./publication";
import type { Models } from "./api";
it("requires exact typed result and visible release readback, distinguishes superseded and missing result", () => {
  const job: Models["JobView"] = {
    job: { id: "job", status: "succeeded" },
    revision: 1,
    progress: {},
    result_ref: null,
    error_code: null,
  };
  expect(publicationState(job)).toBe("missing-result");
  const release: Models["SchemaReleaseView"] = {
    id: "original",
    revision: 1,
    state: "published",
    version: "1",
    document: {
      classes: [],
      properties: [],
      relations: [],
      shapes: "",
      dependencies: [],
    },
    artifacts: [],
  };
  job.result_ref = {
    kind: "schema.release",
    resource_id: "original",
    api_path: "/api/v1/schema/releases/original",
  };
  expect(publicationState(job)).toBe("pending");
  expect(publicationState(job, { ...release, id: "different" })).toBe(
    "missing-result",
  );
  expect(publicationState(job, release)).toBe("published");
  expect(publicationState(job, { ...release, state: "deprecated" })).toBe(
    "superseded",
  );
  expect(
    publicationState(
      { ...job, job: { ...job.job, status: "paused" } },
      release,
    ),
  ).toBe("paused");
  expect(
    publicationState(
      { ...job, job: { ...job.job, status: "failed" } },
      release,
    ),
  ).toBe("failed");
});
