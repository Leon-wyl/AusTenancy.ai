terraform {
  backend "s3" {
    use_lockfile = true
    # bucket, key, region provided via -backend-config at init time
  }
}