# 🌍 Digitalising Industrial Recycling for Data Integrity  
### AI-Powered Document Intelligence, IoT Telemetry & Reconciliation Platform

## 🧠 Executive Summary

Industrial recycling and waste management processes are still heavily dependent on **manual recording, fragmented systems, and paper-based workflows**.

In many organisations today:

- Weight measurements are recorded manually or on paper  
- Vendor invoices are submitted as unstructured documents  
- Operational and financial data are stored in disconnected systems  
- Reconciliation between physical activity and billing is slow and error-prone  

> This creates a fundamental issue: **data integrity is not guaranteed, and operational truth is difficult to verify.**

## 💡 Problem Statement

Without a unified digital system:

- 📉 Operational data is inconsistent or lost  
- 💰 Invoice validation is manual and error-prone  
- 📊 ESG and sustainability reporting lacks auditability  
- 🧾 Disputes arise between vendors and operators due to mismatched records  
- 🕒 Significant human effort is spent on reconciliation instead of optimisation  

> In practice, critical “ground truth” data in recycling workflows is often unreliable.

## 🚀 Solution Overview

This platform demonstrates a **cloud-native, AI-driven digitalisation system** that unifies:

- 📄 Document Intelligence (VLM + RAG) for invoice understanding  
- 🌐 IoT telemetry processing for real-time operational data  
- 🔄 Data reconciliation layer for cross-system consistency  
- ☁️ Event-driven architecture for scalable processing  

Together, these components enable a **traceable, auditable, and intelligent recycling operations platform**.

## 📊 Business Impact

By digitising and correlating operational and financial data, the system enables:

- ✔ Reduced manual reconciliation effort  
- ✔ Improved invoice accuracy and validation  
- ✔ Real-time visibility into recycling operations  
- ✔ Stronger fraud and anomaly detection  
- ✔ Reliable ESG and sustainability reporting  
- ✔ Direct improvement to operational efficiency and bottom line  

> Data integrity becomes a measurable business outcome, not a manual process.

## 🏗️ High-Level Architecture

<img width="4408" height="3284" alt="image" src="https://github.com/user-attachments/assets/fd2384f6-28aa-40b2-af0f-e12e59df35d6" />

This system consists of:

- Serverless ingestion layer (API Gateway + Lambda)  
- Asynchronous processing (SQS + Step Functions)  
- Containerised AI workloads (ECS GPU inference)  
- IoT ingestion and rules processing layer  
- Centralised data and event store  
- Frontend applications for operational visibility  

## 🧩 System Components

### 📄 Files API (Document Intelligence Layer)

Handles:

- Vendor invoice uploads  
- Document storage and preprocessing  
- AI-based extraction using VLM + RAG  
- Integration with downstream workflows  

Key capabilities:

- Multimodal invoice understanding (including handwritten content)  
- Asynchronous event-driven processing  
- Extensible storage and queue abstractions

### 🤖 VLM Workers (AI Processing Layer)

Responsible for:

- Vision-Language Model inference on invoices  
- Structured document extraction  
- GPU-accelerated processing on ECS  
- Asynchronous task execution  

Supports:

- SmolVLM / ColPali-based pipelines  
- Scalable batch inference architecture

### 🌐 IoT System (Operational Telemetry Layer)

Handles:

- IoT device gateway management  
- MQTT-based telemetry ingestion  
- Real-time weight and sensor data capture  
- Rule-based processing engine (Go services)  

Enables:

- Edge-to-cloud data streaming  
- Device-level event tracking  
- Operational visibility across recycling workflows

### 🔄 Data Reconciliation Layer

Ensures consistency across:

- Vendor invoices  
- IoT sensor measurements  
- System-generated events  

Capabilities:

- Cross-source data correlation  
- Detection of mismatches and anomalies  
- Event sourcing for traceability  
- Foundation for audit-ready datasets

## 🧠 AI & Data Intelligence

The platform integrates:

- Vision-Language Models (VLMs) for invoice understanding  
- Retrieval-Augmented Generation (RAG) for document reasoning  
- Event-driven AI pipelines for scalable processing  

This enables:

- Structured extraction from unstructured documents  
- Cross-validation between operational and financial data  
- Intelligent anomaly detection in workflows

## ☁️ Cloud & Infrastructure Design

Built on AWS with a hybrid architecture:

- **Serverless**: API Gateway, Lambda  
- **Containers**: ECS (including GPU inference)  
- **IoT**: AWS IoT Core + custom gateways  
- **Messaging**: SQS + event-driven workflows  
- **Storage**: S3 + central database layer  
- **Scaling**: Auto Scaling Groups + ECS task scaling

## 🔐 Enterprise Readiness (Roadmap)

The system is designed for evolution toward production-grade deployment:

- Private subnet isolation for sensitive workloads  
- IAM-based least privilege access control  
- Structured logging and observability  
- SIEM/UEBA integration for security analytics  
- Persistent vector storage for AI auditability  
- Multi-tenant scalability for enterprise use

## 📈 Strategic Positioning

This platform is designed for:

- ♻️ Recycling and waste management operators  
- 🏭 Industrial logistics and operations teams  
- 🌱 ESG and sustainability reporting initiatives  
- 🏢 Enterprise digital transformation programs  

It represents a **reference architecture for data integrity-driven operational digitalisation*

## 🎥 Demos & Preview

### Full Demo
[![Watch the video](https://i9.ytimg.com/vi_webp/F4hKg0rivH8/mq3.webp?sqp=CJi27M4G-oaymwEmCMACELQB8quKqQMa8AEB-AG2CYAC0AWKAgwIABABGEwgWShlMA8=&rs=AOn4CLCeROCzP9blXzs3AMfn5ME_rZ2dbQ)](https://www.youtube.com/watch?v=F4hKg0rivH8)

### File upload and invoice value extraction
[Extract from digital copies](https://github.com/user-attachments/assets/b7423621-88a8-49a2-aac8-2d39b0a13d63)

[Extract handwritten notes on scanned copies](https://github.com/user-attachments/assets/da86c450-802b-470a-a7d9-2ff1e667bcd3)

![Upload PDF Feature](Upload.png)

![Upload Status Review Feature](Review.png)

### IoT Administration Dashboard
[IoT Administrator Dashboard for managing gateways and End Devices](https://github.com/user-attachments/assets/ee58c146-f114-4bdc-8d97-9df7eb4414dd)




