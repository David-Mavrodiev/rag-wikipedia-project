output "search_endpoint" {
  description = "What STORE_CHOICE=azure-search needs as its endpoint."
  value       = "https://${azurerm_search_service.this.name}.search.windows.net"
}

output "search_service_name" {
  value = azurerm_search_service.this.name
}

output "identity_principal_id" {
  description = "The service's own identity, if something needs to grant it access."
  value       = azurerm_search_service.this.identity[0].principal_id
}

output "sku" {
  description = "Repeated in the output so a paid tier is visible in every plan."
  value       = azurerm_search_service.this.sku
}

output "env_lines" {
  description = "Paste into backend/.env to point the API at this service."
  value       = <<-EOT
    STORE_CHOICE=azure-search
    AZURE_SEARCH_ENDPOINT=https://${azurerm_search_service.this.name}.search.windows.net
    AZURE_SEARCH_INDEX=wikipedia-subset
  EOT
}
