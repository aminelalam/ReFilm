variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Default Google Cloud region."
  type        = string
  default     = "europe-west1"
}

variable "bucket_name" {
  description = "Globally unique Cloud Storage bucket used by ReFilm."
  type        = string
}

variable "render_worker_image" {
  description = "Artifact Registry image for the classic Cloud Run GPU render job. Empty skips job creation."
  type        = string
  default     = ""
}

variable "web_image" {
  description = "Artifact Registry image for the FastAPI Cloud Run service. Empty skips service creation."
  type        = string
  default     = ""
}

variable "enable_cloud_run_jobs" {
  description = "Dispatch heavy renders from the web service to Cloud Run Jobs."
  type        = bool
  default     = false
}

variable "realesrgan_worker_image" {
  description = "Artifact Registry image for the Real-ESRGAN Cloud Run GPU job. Empty skips job creation."
  type        = string
  default     = ""
}

variable "bigquery_location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "EU"
}
