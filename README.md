<!--suppress HtmlDeprecatedAttribute -->
<div align="center">

<h1>
  <img src=".readme/echo-icon.svg" alt="ECHO" width="64" height="64" align="middle">
  ECHO
</h1>

### Emergency Copy Held Offsite

**Self-hosted archival for the data you cannot afford to lose**

<img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white">
<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white">
<img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white">
<img alt="rclone" src="https://img.shields.io/badge/rclone-3F79AD?style=for-the-badge&logo=rclone&logoColor=white">

</div>

> [!WARNING]
> ECHO is under active development. Its configuration, API, database schema, and
> interface may change before the first stable release. Do not use it as the only
> copy of important data.

## Overview

ECHO (Emergency Copy Held Offsite) is an open-source, self-hosted archival project for keeping an independent offsite
copy of important data. It is designed for files that may not need to be immediately accessible, but must survive the
loss, corruption, or compromise of the primary system.

Rather than replacing primary storage or routine backups, ECHO adds a final disaster-recovery layer. It automates
transfers to durable, low-cost remote storage through [rclone](https://rclone.org/), verifies archived copies, and makes
each archive job visible while keeping you in control of your data and storage provider.
