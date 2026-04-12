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

Built on a hybrid architecture that can deploy on a local machine or more importantly, AWS:

- **Serverless**: API Gateway, Lambda  
- **Containers**: ECS (including GPU inference)  
- **IoT**: AWS IoT Core + custom gateways  
- **Messaging**: SQS + event-driven workflows  
- **Storage**: S3 + central database layer  
- **Scaling**: Auto Scaling Groups + ECS task scaling


## 🔐 Enterprise Readiness (High Level Roadmap)

The system is designed as an evolving enterprise-grade platform, progressing from a functional proof-of-concept into a production-ready, scalable, and secure digitalisation solution for industrial recycling and operational data integrity use cases.

### 🛡️ Enterprise Security & Compliance

A core focus of the roadmap is strengthening the platform’s security posture to meet enterprise and regulated environment requirements. This includes:

- Deployment of sensitive workloads (AI inference, reconciliation logic, and core services) within private subnets to reduce exposure and enforce network-level isolation  
- IAM-based least privilege access control across all services to ensure strict identity and permission boundaries between system components and external users  
- Secure external access patterns for vendors and third-party systems through controlled authentication and access layers  
- Integration with SIEM/UEBA systems to enable centralized security monitoring, auditability, and behavioural anomaly detection across system events  
- End-to-end audit logging to ensure traceability of critical actions for compliance, forensic analysis, and ESG reporting requirements  

> Goal: Enable deployment in enterprise, government, and regulated environments where security, auditability, and data governance are mandatory requirements.


### 📊 Observability & Operational Intelligence (SRE Readiness)

The platform is being enhanced with system-wide observability to support production-grade reliability and operational governance:

- Structured logging across all services using consistent event schemas (request tracing, processing states, and system events)  
- CloudWatch-based metrics and log aggregation for centralized monitoring of system health and performance  
- Distributed tracing across event-driven workflows spanning API Gateway, queues, AI processing workers, and database operations  
- Failure visibility and replayability for asynchronous workflows to support debugging, incident response, and system resilience  

> Goal: Provide SRE-level observability, enabling operators to understand system behaviour across distributed, asynchronous pipelines.


### 👥 Multi-Tenancy & Role-Based Access Architecture

To support real-world enterprise adoption, the system is being designed for multi-tenant and role-aware usage:

- Vendor-facing access layer enabling external users to securely upload and manage documents  
- Internal user roles including operators, analysts, and administrators with clearly defined permissions  
- Role-based access control (RBAC) across APIs, data layers, and operational dashboards  
- Tenant-aware data isolation to support potential SaaS-style deployment across multiple organisations or business units  

> Goal: Evolve the platform from a single-deployment system into a scalable multi-tenant architecture suitable for enterprise SaaS adoption.


### 📄 Expansion of Document AI Capabilities

The document intelligence layer is designed to evolve beyond invoice processing into a broader enterprise automation platform:

- Extension of document AI pipelines beyond invoices to include operational documents such as delivery notes, manifests, compliance records, and reports  
- Improved VLM + RAG pipelines for higher robustness, accuracy, and generalisation across document types  
- Structured extraction frameworks that can adapt to domain-specific schemas and enterprise data models  
- Modular architecture to support reuse of document intelligence capabilities across multiple industries and workflows  

> Goal: Transition from invoice-specific automation to a general-purpose enterprise document intelligence platform.


### 🤖 Agentic AI for Data Reconciliation & Automation

A key future direction of the platform is the introduction of agentic AI capabilities to enhance data integrity workflows:

- AI-assisted reconciliation between IoT telemetry data, vendor invoices, and system-generated events  
- Automated anomaly detection across cross-domain datasets (e.g. mismatches between reported weight and sensor readings)  
- Intelligent resolution suggestions for reconciliation conflicts to reduce manual intervention  
- Future exploration of agent-based orchestration to dynamically manage workflow execution and exception handling  

> Goal: Evolve from rule-based reconciliation pipelines into adaptive, AI-driven data integrity systems capable of reducing operational overhead and improving decision quality.


## 📈 Strategic Positioning

This platform is designed for:

- ♻️ Recycling and waste management operators  
- 🏭 Industrial logistics and operations teams  
- 🌱 Sustainability reporting initiatives  
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




