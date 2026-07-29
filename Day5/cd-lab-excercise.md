Extend your GitHub Actions workflow to automatically create and deploy an **Azure App Service (Web App)** pulling the Docker image directly from your Azure Container Registry (ACR).

---

### 1. Prerequisites to Configure in Azure & GitHub

To allow GitHub Actions to create and deploy an Azure Web App using ACR images, you need to set up credentials and permission parameters:

#### A. Storing Azure Service Principal Credentials in GitHub

To allow GitHub Actions to create Azure resources (Resource Group, App Service Plan, Web App), you need an Azure Service Principal secret.

1. **Create Service Principal via Azure CLI:**
```bash
az ad sp create-for-rbac --name "github-actions-appservice" \
                         --role contributor \
                         --scopes /subscriptions/<YOUR_SUBSCRIPTION_ID> \
                         --sdk-auth

```


2. **Add Secret to GitHub:**
* Go to **Settings** > **Secrets and variables** > **Actions** in GitHub.
* Add a new secret named **`AZURE_CREDENTIALS`** and paste the JSON output from the command above.



---

### 2. Updated Workflow File (`build-pyapp-image.yml`)

This updated workflow builds/pushes the Docker image to ACR, creates the required Azure infrastructure if it doesn't exist (Resource Group, App Service Plan, Web App), configures the Web App with ACR admin credentials, and triggers the deployment.

```yaml
name: Docker Image CI and Azure Web App Deployment

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]

env:
  ACR_NAME: oakcrdev
  IMAGE_NAME: python-app
  RESOURCE_GROUP: rg-python-app-demo
  LOCATION: eastus
  APP_SERVICE_PLAN: plan-python-app
  WEB_APP_NAME: webapp-python-app-demo  # Note: Must be globally unique across Azure

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Check out the repo
        uses: actions/checkout@v4

      - name: Log in to Azure Container Registry
        uses: azure/docker-login@v1
        with:
          login-server: ${{ env.ACR_NAME }}.azurecr.io
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}

      - name: Build and Push Docker image
        run: |
          # Generate unique image tag using timestamp
          IMAGE_TAG="${{ env.ACR_NAME }}.azurecr.io/${{ env.IMAGE_NAME }}:${{ github.run_number }}"
          
          # Build context pointing to application directory
          docker build -t $IMAGE_TAG Day4/python/api
          
          # Push image to ACR
          docker push $IMAGE_TAG

      - name: Azure Login
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Provision Azure Infrastructure & Web App
        uses: azure/cli@v2
        with:
          inlineScript: |
            # 1. Create Resource Group
            az group create --name ${{ env.RESOURCE_GROUP }} --location ${{ env.LOCATION }}

            # 2. Create App Service Plan (Linux container worker)
            az appservice plan create \
              --name ${{ env.APP_SERVICE_PLAN }} \
              --resource-group ${{ env.RESOURCE_GROUP }} \
              --sku B1 \
              --is-linux

            # 3. Create Web App configured with the container image
            IMAGE_FULL_NAME="${{ env.ACR_NAME }}.azurecr.io/${{ env.IMAGE_NAME }}:${{ github.run_number }}"
            
            if ! az webapp show --name ${{ env.WEB_APP_NAME }} --resource-group ${{ env.RESOURCE_GROUP }} &>/dev/null; then
              echo "Creating Azure Web App..."
              az webapp create \
                --resource-group ${{ env.RESOURCE_GROUP }} \
                --plan ${{ env.APP_SERVICE_PLAN }} \
                --name ${{ env.WEB_APP_NAME }} \
                --deployment-container-image-name $IMAGE_FULL_NAME
            fi

            # 4. Configure Web App to authenticate against ACR using admin credentials
            az webapp config container set \
              --name ${{ env.WEB_APP_NAME }} \
              --resource-group ${{ env.RESOURCE_GROUP }} \
              --docker-custom-image-name $IMAGE_FULL_NAME \
              --docker-registry-server-url "https://${{ env.ACR_NAME }}.azurecr.io" \
              --docker-registry-server-user "${{ secrets.ACR_USERNAME }}" \
              --docker-registry-server-password "${{ secrets.ACR_PASSWORD }}"

      - name: Deploy to Azure Web App
        uses: azure/webapps-deploy@v3
        with:
          app-name: ${{ env.WEB_APP_NAME }}
          images: '${{ env.ACR_NAME }}.azurecr.io/${{ env.IMAGE_NAME }}:${{ github.run_number }}'

```

---

### Workflow Enhancements

1. **`azure/login@v2`**: Logs into Azure via the Service Principal credentials stored in `AZURE_CREDENTIALS`.
2. **`az appservice plan create --is-linux`**: Container-based Web Apps in Azure require a Linux-based App Service Plan.
3. **`az webapp config container set`**: Passes the ACR admin credentials (`ACR_USERNAME` and `ACR_PASSWORD`) directly to Azure Web App settings so Azure has permission to pull the container image from your registry.
4. **`azure/webapps-deploy@v3`**: Explicitly notifies Azure App Service to restart and serve the newly built image tag (`github.run_number`).