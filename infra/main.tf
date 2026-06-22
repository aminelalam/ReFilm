terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "videointelligence.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "archive" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["processed/", "scenes/"]
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["dataset/"]
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_pubsub_topic" "video_jobs" {
  name = "refilm-video-jobs"
}

resource "google_artifact_registry_repository" "workers" {
  location      = var.region
  repository_id = "refilm-workers"
  format        = "DOCKER"
}

resource "google_service_account" "worker" {
  account_id   = "refilm-worker"
  display_name = "ReFilm batch worker"
}

resource "google_service_account" "web" {
  account_id   = "refilm-web"
  display_name = "ReFilm web API"
}

resource "google_project_iam_member" "worker_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_bigquery" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_bigquery_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "web_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "web_bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "web_bigquery_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "web_run_jobs" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_cloud_run_v2_service" "web" {
  count    = var.web_image == "" ? 0 : 1
  name     = "refilm-web"
  location = var.region

  template {
    service_account = google_service_account.web.email

    containers {
      image = var.web_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "REFILM_CLOUD_ENABLED"
        value = "true"
      }
      env {
        name  = "REFILM_CLOUD_RUN_JOBS_ENABLED"
        value = var.enable_cloud_run_jobs ? "true" : "false"
      }
      env {
        name  = "REFILM_CLOUD_RUN_JOB_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCS_BUCKET_NAME"
        value = var.bucket_name
      }
      env {
        name  = "BQ_LOCATION"
        value = var.bigquery_location
      }
    }
  }
}

resource "google_cloud_run_v2_job" "render_gpu" {
  count    = var.render_worker_image == "" ? 0 : 1
  name     = "refilm-render-gpu"
  location = var.region

  template {
    template {
      service_account               = google_service_account.worker.email
      timeout                       = "86400s"
      max_retries                   = 1
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = "nvidia-l4"
      }

      containers {
        name  = "worker"
        image = var.render_worker_image
        args  = ["--help"]

        resources {
          limits = {
            cpu              = "8"
            memory           = "32Gi"
            "nvidia.com/gpu" = "1"
          }
        }

        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "BQ_LOCATION"
          value = var.bigquery_location
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job" "realesrgan_gpu" {
  count    = var.realesrgan_worker_image == "" ? 0 : 1
  name     = "refilm-realesrgan-gpu"
  location = var.region

  template {
    template {
      service_account               = google_service_account.worker.email
      timeout                       = "86400s"
      max_retries                   = 1
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = "nvidia-l4"
      }

      containers {
        name  = "worker"
        image = var.realesrgan_worker_image
        args  = ["--help"]

        resources {
          limits = {
            cpu              = "8"
            memory           = "32Gi"
            "nvidia.com/gpu" = "1"
          }
        }

        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "BQ_LOCATION"
          value = var.bigquery_location
        }
      }
    }
  }
}

resource "google_bigquery_dataset" "audit" {
  dataset_id = "refilm_audit"
  location   = var.bigquery_location
}

resource "google_bigquery_table" "jobs" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "jobs"

  schema = jsonencode([
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "filename", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "colorize", type = "BOOL", mode = "NULLABLE" },
    { name = "processing_profile", type = "STRING", mode = "NULLABLE" },
    { name = "media_type", type = "STRING", mode = "NULLABLE" },
    { name = "color_mode", type = "STRING", mode = "NULLABLE" },
    { name = "color_style", type = "STRING", mode = "NULLABLE" },
    { name = "model_name", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "updated_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "original_uri", type = "STRING", mode = "NULLABLE" },
    { name = "final_uri", type = "STRING", mode = "NULLABLE" },
    { name = "comparison_uri", type = "STRING", mode = "NULLABLE" },
    { name = "audit_uri", type = "STRING", mode = "NULLABLE" },
    { name = "error", type = "STRING", mode = "NULLABLE" },
    { name = "details_json", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "job_files" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "job_files"

  schema = jsonencode([
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "category", type = "STRING", mode = "NULLABLE" },
    { name = "filename", type = "STRING", mode = "NULLABLE" },
    { name = "gcs_uri", type = "STRING", mode = "REQUIRED" },
    { name = "content_type", type = "STRING", mode = "NULLABLE" },
    { name = "size_bytes", type = "INT64", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "processing_steps" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "processing_steps"

  schema = jsonencode([
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "scene_id", type = "STRING", mode = "NULLABLE" },
    { name = "step_name", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "input_uri", type = "STRING", mode = "NULLABLE" },
    { name = "output_uri", type = "STRING", mode = "NULLABLE" },
    { name = "started_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "finished_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "details_json", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "metrics" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "metrics"

  schema = jsonencode([
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "metric_name", type = "STRING", mode = "REQUIRED" },
    { name = "metric_value", type = "FLOAT64", mode = "NULLABLE" },
    { name = "details_json", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "video_shots" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "video_shots"

  schema = jsonencode([
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "shot_id", type = "STRING", mode = "REQUIRED" },
    { name = "start_time_seconds", type = "FLOAT64", mode = "NULLABLE" },
    { name = "end_time_seconds", type = "FLOAT64", mode = "NULLABLE" },
    { name = "duration_seconds", type = "FLOAT64", mode = "NULLABLE" },
    { name = "source_uri", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "vertex_model_runs" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "vertex_model_runs"

  schema = jsonencode([
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "model_name", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE" },
    { name = "task_type", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "input_uri", type = "STRING", mode = "NULLABLE" },
    { name = "output_uri", type = "STRING", mode = "NULLABLE" },
    { name = "vertex_custom_job_name", type = "STRING", mode = "NULLABLE" },
    { name = "machine_type", type = "STRING", mode = "NULLABLE" },
    { name = "accelerator_type", type = "STRING", mode = "NULLABLE" },
    { name = "accelerator_count", type = "INT64", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "started_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "finished_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "details_json", type = "STRING", mode = "NULLABLE" },
    { name = "error", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "restoration_metrics" {
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "restoration_metrics"

  schema = jsonencode([
    { name = "metric_id", type = "STRING", mode = "REQUIRED" },
    { name = "job_id", type = "STRING", mode = "REQUIRED" },
    { name = "comparison_type", type = "STRING", mode = "NULLABLE" },
    { name = "baseline_label", type = "STRING", mode = "NULLABLE" },
    { name = "candidate_label", type = "STRING", mode = "NULLABLE" },
    { name = "baseline_uri", type = "STRING", mode = "NULLABLE" },
    { name = "candidate_uri", type = "STRING", mode = "NULLABLE" },
    { name = "sampled_frames", type = "INT64", mode = "NULLABLE" },
    { name = "baseline_width", type = "INT64", mode = "NULLABLE" },
    { name = "baseline_height", type = "INT64", mode = "NULLABLE" },
    { name = "candidate_width", type = "INT64", mode = "NULLABLE" },
    { name = "candidate_height", type = "INT64", mode = "NULLABLE" },
    { name = "baseline_fps", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_fps", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_duration_seconds", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_duration_seconds", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_sharpness", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_sharpness", type = "FLOAT64", mode = "NULLABLE" },
    { name = "sharpness_gain", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_contrast", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_contrast", type = "FLOAT64", mode = "NULLABLE" },
    { name = "contrast_gain", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_brightness", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_brightness", type = "FLOAT64", mode = "NULLABLE" },
    { name = "brightness_delta", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_noise_estimate", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_noise_estimate", type = "FLOAT64", mode = "NULLABLE" },
    { name = "noise_delta", type = "FLOAT64", mode = "NULLABLE" },
    { name = "baseline_flicker_estimate", type = "FLOAT64", mode = "NULLABLE" },
    { name = "candidate_flicker_estimate", type = "FLOAT64", mode = "NULLABLE" },
    { name = "flicker_delta", type = "FLOAT64", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "details_json", type = "STRING", mode = "NULLABLE" },
  ])
}
