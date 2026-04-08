# SnowDesk Data Pipeline

This is the data ingestion and processing pipeline for Snowdesk. It is a Django
project that pulls Avalanche bulletin data from SLF, processes it and stores it
locally for presentation to the end user.

The Snowdesk website is a separate project that uses the data in this project, which is presented as a readonly data API.

The current source is SLF (Swiss Avalanche service). This data is stored
unchanged - it is the canonical data.

## Data format

The SLF data is provided in CAAML format - which is GeoJSON compatible.

---

*SnowDesk is a personal project built to find its audience and serve it well. Feedback, corrections, and conversations are welcome.*
