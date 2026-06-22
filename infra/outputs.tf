output "archive_bucket" {
  value = google_storage_bucket.archive.name
}

output "artifact_registry_repository" {
  value = "${google_artifact_registry_repository.workers.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.workers.repository_id}"
}

output "web_service_uri" {
  value = var.web_image == "" ? null : google_cloud_run_v2_service.web[0].uri
}

output "render_job_name" {
  value = var.render_worker_image == "" ? null : google_cloud_run_v2_job.render_gpu[0].name
}

output "realesrgan_job_name" {
  value = var.realesrgan_worker_image == "" ? null : google_cloud_run_v2_job.realesrgan_gpu[0].name
}
