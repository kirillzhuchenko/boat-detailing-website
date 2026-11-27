variable "resource_group_name" {
  default = "WestUS_2"
}

variable "location" {
  default = "westus2"
}

variable "vnet_addr" {
  default = ["10.0.0.0/16"]
}

variable "pub_subnet" {
  default = ["10.0.1.0/24"]
}

variable "priv_subnet_app" {
  default = ["10.0.2.0/24"]
}

variable "priv_subnet_db" {
  default = ["10.0.3.0/24"]
}

variable "pub_rt_hop_type" {
  default = "Internet"
}

variable "db_version" {
  default = "18"
}

variable "db_login" {
  type      = string
  sensitive = true
}

variable "db_pass" {
  type      = string
  sensitive = true
}

variable "db_name" {
  type      = string
  sensitive = true
}

variable "db_server_name" {
  default = "db-server-westus2"
}

variable "db_sku_name" {
  default = "B_Standard_B2s"
}

variable "db_storage_mb" {
  default = 32768
}

variable "db_storage_tier" {
  default = "P4"
}

variable "db_storage_type" {
  default = "Premium_LRS"
}

variable "db_create_mode" {
  default = "Default"
}

variable "priv_dns_zone" {
  default = "bw.postgres.database.azure.com"
}

variable "username" {
  default = "adminuser"
}

variable "vm_cale_set_sku" {
  default = "Standard_D2alds_v7"
}

variable "vm_sir_publisher" {
  default = "canonical"
}

variable "vm_sir_sku" {
  default = "minimal"
}

variable "vm_sir_ver" {
  default = "26.10.202610110"
}

variable "vm_sir_offer" {
  default = "ubuntu-26_10"
}

variable "vm_disk_size" {
  default = 30
}

variable "vm_disk_caching" {
  default = "ReadWrite"
}

variable "vm_storage_type" {
  default = "StandardSSD_LRS"
}

variable "lb_size" {
  default = "Standard_D2alds_v7"
}

variable "lb_storage_type" {
  default = "Standard_LRS"
}

variable "lb_sir_publisher" {
  default = "canonical"
}

variable "lb_sir_sku" {
  default = "minimal"
}

variable "lb_sir_ver" {
  default = "26.10.202610110"
}

variable "lb_sir_offer" {
  default = "ubuntu-26_10"
}

variable "app_port" {
  default = 5000
}

variable "def_protocol" {
  default = "Tcp"
}

variable "db_port" {
  default = 5432
}

variable "lb_ip_alloc" {
  default = "Dynamic"
}

variable "dockerhub_username" {
  default = "kirillzhuchenko97"
}

variable "dockerhub_token" {
  type      = string
  sensitive = true
}

variable "app_image" {
  default = "kirillzhuchenko97/boat-detailing-website:feature-40e0d5792ea8750c1f5445edc70f9c7a316efe76"
}

variable "rev_proxy" {
  default = "dhi.io/nginx:1-alpine"
}

variable "first_public_key" {
  type      = string
  sensitive = true
}