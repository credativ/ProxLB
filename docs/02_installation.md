# Table of Contents

- [Installation](#installation)
  - [Requirements / Dependencies](#requirements--dependencies)
  - [Debian Package](#debian-package)
    - [Quick-Start](#quick-start)
    - [Details](#details)
    - [Debian Packages (.deb files)](#debian-packages-deb-files)
    - [Repo Mirror and Proxmox Offline Mirror Support](#repo-mirror-and-proxmox-offline-mirror-support)
  - [RedHat Package](#redhat-package)
  - [Container Images / Docker](#container-images--docker)
    - [Overview of Images](#overview-of-images)
  - [Source](#source)
    - [Traditional System](#traditional-system)
    - [Container Image](#container-image)
- [Upgrading](#upgrading)
  - [Upgrading from < 1.1.0](#upgrading-from--110)
  - [Upgrading from >= 1.1.0](#upgrading-from--110)


## Installation
### Requirements / Dependencies
* Python3.x
* proxmoxer
* requests
* urllib3
* pyyaml

The dependencies can simply be installed with `pip` by running the following command:
```
pip install -r requirements.txt
```

*Note: Distribution packages, such like the provided `.deb` package will automatically resolve and install all required dependencies by using already packaged version from the distribution's repository. By using the Docker (container) image or Debian packages, you do not need to take any care of the requirements listed here.*

### Debian Package
ProxLB is a powerful and flexible load balancer designed to work across various architectures, including `amd64`, `arm64`, `rv64` and many other ones that support Python. It runs independently of the underlying hardware, making it a versatile choice for different environments. This chapter covers the step-by-step process to install ProxLB on Debian-based systems, including Debian clones like Ubuntu.

#### Quick-Start
You can simply use this snippet to install the repository and to install ProxLB on your system.

```bash
# Add GPG key
curl -fsSL https://packages.credativ.com/public/proxtools/public.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/proxtools-archive-keyring.gpg

# Add repository
echo "deb [signed-by=/etc/apt/keyrings/proxtools-archive-keyring.gpg] \
https://packages.credativ.com/public/proxtools stable main" \
| sudo tee /etc/apt/sources.list.d/proxlb.list

# Update & install
sudo apt-get update
sudo apt-get -y install proxlb

# Copy example config
sudo cp /etc/proxlb/proxlb_example.yaml /etc/proxlb/proxlb.yaml

# Adjust the config to your needs
sudo vi /etc/proxlb/proxlb.yaml

# Start service
sudo systemctl start proxlb

# Adjust the config to your needs
sudo vi /etc/proxlb/proxlb.yaml
sudo systemctl start proxlb
```

Afterwards, ProxLB is running in the background and balances your cluster by your defined balancing method (default: memory).

#### Details
ProxLB provides two different repositories:
* https://packages.credativ.com/public/proxtools stable main
* https://packages.credativ.com/public/proxtools snapshots main

The repository is signed and the GPG key can be found at:
* https://packages.credativ.com/public/proxtools/archive-keyring.gpg

You can also simply import it by running:

```
# KeyID:  34C5B9642CD591E5D090A03B062A8A3A410B831D
# UID:    Proxtools Repository Signer <info@credativ.de>
# SHA256: 4cb4a74b25f775616709eb0596eeeac61d8d28717f4872fef2d68fb558434ed3  public.key

wget -O /etc/apt/keyrings/proxtools-archive-keyring.gpg https://packages.credativ.com/public/proxtools/public.key
```

### Container Images / Docker
Using the ProxLB container images is straight forward and only requires you to mount the config file.

Available images can be found at the GitHub [packages page](https://github.com/credativ/ProxLB/pkgs/container/proxlb) or [Docker Hub](https://hub.docker.com/r/credativ/proxlb)..

```bash
# Pull the image from GHCR
docker pull ghcr.io/credativ/proxlb:latest
# or Docker Hub
docker pull credativ/proxlb:latest
# Download the config
wget -O proxlb.yaml https://raw.githubusercontent.com/gyptazy/ProxLB/refs/heads/main/config/proxlb_example.yaml
# Adjust the config to your needs
vi proxlb.yaml
# Start the ProxLB container image with the ProxLB config
docker run -it --rm -v $(pwd)/proxlb.yaml:/etc/proxlb/proxlb.yaml proxlb
```

*Note: ProxLB container images are officially only available at ghcr.io/credativ/proxlb or docker.io/credativ/proxlb*

### Source
ProxLB can also easily be used from the provided sources - for traditional systems but also as a Docker/Podman container image.

#### Traditional System
Setting up and running ProxLB from the sources is simple and requires just a few commands. Ensure Python 3 and the Python dependencies are installed on your system, then run ProxLB using the following command:
```bash
git clone https://github.com/gyptazy/ProxLB.git
cd ProxLB
```

Afterwards simply adjust the config file to your needs:
```bash
vi config/proxlb.yaml
```

Start ProxLB by Python3 on the system:
```bash
python3 proxlb/main.py -c config/proxlb.yaml
```

#### Container Image
Creating a container image of ProxLB is straightforward using the provided Dockerfile. The Dockerfile simplifies the process by automating the setup and configuration required to get ProxLB running in an Alpine container. Simply follow the steps in the Dockerfile to build the image, ensuring all dependencies and configurations are correctly applied. For those looking for an even quicker setup, a ready-to-use ProxLB container image is also available, eliminating the need for manual building and allowing for immediate deployment.

```bash
git clone https://github.com/gyptazy/ProxLB.git
cd ProxLB
docker build -t proxlb .
```

Afterwards simply adjust the config file to your needs:
```bash
vi config/proxlb.yaml
```

Finally, start the created container.
```bash
docker run -it --rm -v $(pwd)/proxlb.yaml:/etc/proxlb/proxlb.yaml proxlb
```

## Upgrading
### Upgrading from < 1.1.0
Upgrading ProxLB is not supported due to a fundamental redesign introduced in version 1.1.x. With this update, ProxLB transitioned from a monolithic application to a pure Python-style project, embracing a more modular and flexible architecture. This shift aimed to improve maintainability and extensibility while keeping up with modern development practices. Additionally, ProxLB moved away from traditional ini-style configuration files and adopted YAML for configuration management. This change simplifies configuration handling, reduces the need for extensive validation, and ensures better type casting, ultimately providing a more streamlined and user-friendly experience.

### Upgrading from >= 1.1.0
Uprading within the current stable versions, starting from 1.1.0, will be possible in all supported ways.