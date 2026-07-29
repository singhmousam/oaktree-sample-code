### Day 4: Lab Excercise
- Connect to VDI machine
- Clone the public repo
    ```git clone repoUrl```
- Spin up given docker container
- Connect to ACR repository (Use existing or create a new one individually)
- Push image to ACR
    ``` docker tag {your_image} oakcrdev.azurecr.io/{your_image}:v1
    docker push oakcrdev.azurecr.io/{your_image}:v1
    ```
- Deploy WebApp with minimum configuration using the image

Prerequisites:
- Azure Access
- Resource Group creation
