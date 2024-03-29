# Setup script for explain_adversarial.py
# Train classification model on imagenette and perform fgsm attack

# %%
# Imports
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms
from torchvision.io import read_image
from torchvision.models import resnet50, ResNet50_Weights
import numpy as np
import matplotlib.pyplot as plt
from tqdm.notebook import tqdm
from pathlib import Path
from PIL import Image
import cv2
from pytorch_grad_cam import CAM
from pytorch_grad_cam.utils.image import show_cam_on_image

# %%
# Set main path to repo root directory
main = Path(".").resolve()
main

# %%
# Load imagenette trainset
transform=transforms.Compose([transforms.ToTensor(), transforms.Resize((400, 600))])
train_dataset = datasets.Imagenette(main / "data", size='full',
                                    split='train', transform=transform, download=False)
train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=16, shuffle=True)

# %%
# Finetune ResNet50 on Imagenette
weights = ResNet50_Weights.DEFAULT
model = resnet50(weights=weights)

print("CUDA Available: ", torch.cuda.is_available())
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)

for param in model.parameters():
    param.requires_grad = False
model.fc = nn.Linear(model.fc.in_features, len(train_dataset.classes))
model.train()

criterion = nn.CrossEntropyLoss()
optimizer = optim.SGD(model.fc.parameters(), lr=0.001, momentum=0.9)

num_epochs = 2

for epoch in range(num_epochs):
    with tqdm(train_loader, desc=f"Train Ep {epoch + 1}", total=len(train_loader)) as tq:
        for images, targets in tq:
            x, y = images.to(device), targets.to(device)
            optimizer.zero_grad()
            y_hat = model(x)
            loss = criterion(y_hat, y)
            loss.backward()
            optimizer.step()

torch.save(model.state_dict(), main / "checkpoints/resnet50_imagenette.pth")

# %%
# Load imagenette testset
transform=transforms.Compose([transforms.ToTensor(), transforms.Resize((400, 600))])
test_dataset = datasets.Imagenette(main / "data", size='full',
                                   split='val', transform=transform, download=False)
test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=16, shuffle=False)

# %%
# Load checkpoint
checkpoint = torch.load(main / "checkpoints/resnet50_imagenette.pth")
model = resnet50()

print("CUDA Available: ", torch.cuda.is_available())
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)

model.fc = nn.Linear(model.fc.in_features, len(test_dataset.classes))
model.load_state_dict(checkpoint)
model.eval()

# %%
# Evaluate ResNet50
all_predictions = []
all_labels = []
with tqdm(test_loader, desc=f"Eval", total=len(test_loader)) as tq:
    for images, targets in tq:
        with torch.no_grad():
            x, y = images.to(device), targets.to(device)
            predictions = model(x).argmax(1).tolist()
            labels = y.tolist()
            all_predictions += predictions
            all_labels += labels
all_predictions = torch.Tensor(all_predictions)
all_labels = torch.Tensor(all_labels)
accuracy = torch.sum(torch.eq(all_predictions, all_labels)).item() / all_predictions.size(-1)
# Accuracy = 0.979108280254777
print(f"Accuracy: {accuracy}")

# %%
def fgsm_attack(image, epsilon, data_grad):
    sign_data_grad = data_grad.sign()
    perturbed_image = image + epsilon * sign_data_grad
    perturbed_image = torch.clamp(perturbed_image, 0, 1)
    return perturbed_image

# %%
# Define custom dataset that additionally returns image path
class ImagenettePath(datasets.Imagenette):
    def __getitem__(self, idx):
        path, label = self._samples[idx]
        image = Image.open(path).convert("RGB")
        if self.transform is not None:
            image = self.transform(image)
        if self.target_transform is not None:
            label = self.target_transform(label)
        return image, label, path

# %%
# Helper function that converts an image tensor to displayable format
def convert(input_image, multiply=False, BGR=False):
    multiplier = 255.0 if multiply else 1.0
    image = np.moveaxis(input_image[0].detach().cpu().numpy() * multiplier, 0, 2)
    if BGR:
        image = image[:, :, ::-1]
    return image

# %%
# Get a new copy of imagenette; then load testset with ImagenettePath
transform=transforms.Compose([transforms.ToTensor(), transforms.Resize((400, 600))])
perturbing_dataset = ImagenettePath(main / "perturbed_data", size='full',
                                    split='val', transform=transform, download=False)
perturbing_loader = torch.utils.data.DataLoader(perturbing_dataset, batch_size=1, shuffle=False)

# %%
# Perturb images in testset; report accuracy
criterion = nn.CrossEntropyLoss()
epsilon = 0.3

all_predictions = []
all_labels = []
with tqdm(perturbing_loader, desc=f"Attack", total=len(perturbing_loader)) as tq:
    for image, target, path in tq:
        x, y = image.to(device), target.to(device)
        x.requires_grad = True
        y_hat = model(x)
        prediction = y_hat.argmax(1)
        if prediction.item() != y.item():
            continue

        loss = criterion(y_hat, target)
        model.zero_grad()
        loss.backward()
        perturbed_image = fgsm_attack(image, epsilon, image.grad.data)

        path_split = path[0].split("/")
        path_attacking = "/".join(path_split[:-1]) + "/" + path_split[-1][:-5] + "_attacked.JPEG"
        cv2.imwrite(path_attacking, convert(perturbed_image, multiply=True, BGR=True))

        perturbed_y_hat = model(perturbed_image)
        perturbed_prediction = perturbed_y_hat.argmax(1).tolist()
        label = y.tolist()

        all_predictions += perturbed_prediction
        all_labels += label
all_predictions = torch.Tensor(all_predictions)
all_labels = torch.Tensor(all_labels)
accuracy = torch.sum(torch.eq(all_predictions, all_labels)).item() / all_predictions.size(-1)
# Accuracy = 0.11137132448607859
print(f"Accuracy: {accuracy}")

# %%
# Load perturbed testset
transform=transforms.Compose([transforms.ToTensor(), transforms.Resize((400, 600))])
perturbed_dataset = datasets.Imagenette(main / "perturbed_data", size='full',
                                        split='val', transform=transform, download=False)
perturbed_loader = torch.utils.data.DataLoader(perturbed_dataset, batch_size=1, shuffle=True)

# %%
# Compute saliency maps
with tqdm(perturbed_loader, desc=f"Saliency", total=len(perturbed_loader)) as tq:
    i = 0
    for image, target in tq:
        i += 1
        target_layer = model.layer4[-1]
        cam = CAM(model=model,  target_layer=target_layer, use_cuda=torch.cuda.is_available())
        grayscale_cam = cam(input_tensor=image, target_category=target.item(), method="gradcam")
        converted_image = convert(image)
        plt.figure()
        plt.imshow(converted_image)
        plt.figure()
        visualization = show_cam_on_image(converted_image, grayscale_cam)
        plt.imshow(visualization)
        if i == 10:
            break

# %%
