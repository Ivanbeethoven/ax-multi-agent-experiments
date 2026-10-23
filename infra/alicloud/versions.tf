terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "= 1.274.0"
    }
  }
}

# Credentials are obtained from the environment, STS, or an existing profile.
provider "alicloud" {
  region = var.region
}
