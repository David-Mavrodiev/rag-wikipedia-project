# cp example.tfvars my.tfvars && terraform plan -var-file=my.tfvars
prefix   = "rag-wikipedia-hja"
location = "francecentral"

# Free: 50 MB, 3 indexes, enough for the ACL-trimming subset.
sku = "free"

# Only needed for a billable SKU:
# sku               = "basic"
# confirm_paid_tier = true

# Object ids (az ad signed-in-user show --query id -o tsv)
# reader_principal_id  = ""
# indexer_principal_id = ""
