"""Summary: Azure runtime fields mixed into the backend application settings.

Key classes:
- AzureRuntimeFields: non-secret Azure resource and managed-identity configuration.

Key functions:
- (none)

Notes:
- Credentials remain environment-injected; this model contains only endpoints and resource names.
"""

from pydantic import BaseModel, Field


class AzureRuntimeFields(BaseModel):
    """Typed Azure runtime settings shared by AppSettings."""

    azure_managed_identity_token_url: str = Field(
        default="",
        description="Managed-identity token endpoint URL, supplied by config/env in Azure.",
    )
    azure_managed_identity_api_version: str = Field(
        default="2018-02-01",
        description="Managed-identity token API version used against the IMDS token endpoint.",
    )
    azure_container_apps_identity_api_version: str = Field(
        default="2019-08-01",
        description=(
            "Managed-identity token API version used against the Container Apps identity endpoint."
        ),
    )
    azure_managed_identity_client_id: str | None = Field(
        default=None,
        description="User-assigned identity client id for Azure data/control-plane calls.",
    )
    azure_arm_endpoint: str = Field(
        default="",
        description="Azure Resource Manager endpoint base URL, supplied by config/env.",
    )
    azure_arm_token_resource: str = Field(
        default="",
        description="Token resource/audience for Azure Resource Manager.",
    )
    azure_subscription_id: str | None = Field(
        default=None,
        description="Azure subscription id containing the Container Apps Jobs.",
    )
    azure_resource_group_name: str | None = Field(
        default=None,
        description="Azure resource group containing the Container Apps Jobs.",
    )
    azure_container_apps_api_version: str = Field(
        default="2024-03-01",
        description="Azure Container Apps Jobs ARM API version.",
    )
    azure_container_apps_retrain_job_name: str | None = Field(
        default=None,
        description="Container Apps Job name for model retraining.",
    )
    azure_container_apps_batch_score_job_name: str | None = Field(
        default=None,
        description="Container Apps Job name for batch scoring.",
    )
    azure_storage_account_name: str | None = Field(
        default=None,
        description="Azure Storage account name for artifact and SAR-PDF blobs.",
    )
    azure_storage_blob_host_suffix: str = Field(
        default="blob.core.windows.net",
        description="Azure Blob DNS suffix used to build the storage endpoint.",
    )
    azure_storage_blob_endpoint: str | None = Field(
        default=None,
        description="Optional Azure Blob endpoint; otherwise derived from the account name.",
    )
    azure_storage_token_resource: str = Field(
        default="",
        description="Token resource/audience for Azure Blob Storage.",
    )
    azure_storage_container_name: str = Field(
        default="artifacts",
        description="Blob container for model/artifact keys.",
    )
    azure_storage_sar_pdf_container_name: str = Field(
        default="sar-pdfs",
        description="Blob container for SAR PDF keys.",
    )
    azure_storage_blob_api_version: str = Field(
        default="2023-11-03",
        description="Azure Blob data-plane API version.",
    )
    azure_rest_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for Azure managed-identity, Blob, and ARM REST calls.",
    )
