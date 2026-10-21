import type { Models } from "./api";
export function publicationState(
  job: Models["JobView"],
  release?: Models["SchemaReleaseView"],
) {
  if (["failed", "paused", "cancelled"].includes(job.job.status))
    return job.job.status;
  if (job.job.status !== "succeeded") return "pending";
  const result = job.result_ref;
  if (
    !result ||
    result.kind !== "schema.release" ||
    result.api_path !== `/api/v1/schema/releases/${result.resource_id}`
  )
    return "missing-result";
  if (!release) return "pending";
  if (release.id !== result.resource_id) return "missing-result";
  if (release.state === "published") return "published";
  if (release.state === "deprecated") return "superseded";
  return release.state === "failed" ? "failed" : "pending";
}
