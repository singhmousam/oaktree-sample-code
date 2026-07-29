## Designing CI Pipeline for GitHub
### 1. Storing ACR Admin Credentials in GitHub

For training/lab purposes, storing the ACR admin username and password securely is done via **GitHub Repository Secrets**.

#### Step 1: Get ACR Admin Credentials

1. Open the **Azure Portal**, go to your **Container Registry** (`oakcrdev`).
2. Under **Settings**, select **Access keys**.
3. Enable **Admin user**.
4. Copy the **Username** (usually `oakcrdev`) and **password**.

*(Alternatively via Azure CLI: `az acr credential show --name oakcrdev`)*

#### Step 2: Store Credentials in GitHub

1. In your GitHub repository (`OAKTREE-SAMPLE-CODE`), go to **Settings** > **Secrets and variables** > **Actions**.
2. Click **New repository secret** and add:
* **`ACR_USERNAME`**: `oakcrdev`
* **`ACR_PASSWORD`**: *(Your ACR admin password)*



---

### 2. Analysis of the Directory Issue

In your image, line 18 runs:

```bash
docker build . --file Day4/python/api/Dockerfile --tag oakcrdev.azurecr.io/python-app:$(date +%s)

```

**Pointers:**

1. **Build Context (`.`):** Setting context to `.` (the root of the repo) makes Docker look for files relative to root. When `Dockerfile` commands like `COPY . .` run inside `Day4/python/api/Dockerfile`, Docker will copy the entire repository root instead of just the Python application files.
2. **Missing Login & Push Step:** The current workflow only builds the image locally on the runner and does not log in to ACR or push the built image.

---

### 3. Workflow file `.github/workflows/build-pyapp-image.yml`

```yaml
name: Docker Image CI

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Check out the repo
        uses: actions/checkout@v4

      - name: Log in to Azure Container Registry
        uses: azure/docker-login@v1
        with:
          login-server: oakcrdev.azurecr.io
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}

      - name: Build and Push Docker image
        run: |
          # Set the build tag using timestamp
          IMAGE_TAG="oakcrdev.azurecr.io/python-app:$(date +%s)"
          
          # Build passing the app folder as context to prevent directory errors
          docker build -t $IMAGE_TAG Day4/python/api
          
          # Push the image to Azure Container Registry
          docker push $IMAGE_TAG

```