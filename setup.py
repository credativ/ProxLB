from pathlib import Path
from setuptools import setup

long_description = (Path(__file__).parent / 'README.md').read_text(encoding='utf-8')

setup(
    name="proxlb",
    version="2.2.0",
    description="An advanced resource scheduler and load balancer for Proxmox clusters.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Florian Paul Azim Hoberg",
    author_email="gyptazy@gyptazy.com",
    maintainer="credativ GmbH",
    maintainer_email="support@credativ.de",
    url="https://github.com/credativ/ProxLB",
    packages=["proxlb", "proxlb.utils", "proxlb.models"],
    install_requires=[
        "packaging",
        "proxlb-solver>=0.1.1",
        "proxmoxer",
        "pydantic",
        "pyyaml",
        "requests",
        "urllib3",
    ],
    data_files=[('/etc/systemd/system', ['service/proxlb.service']), ('/etc/proxlb/', ['config/proxlb_example.yaml'])],
)
