'use client';

import React, { useState, useRef, useEffect } from 'react';
import { X, Upload, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription } from './alert';
import { api } from '../../lib/api_client';
import type { InvoiceUploadParams, InvoiceUploadData } from '../../types/invoice';

const API_BASE_URL = 'http://localhost:8000';

interface PDFFile {
  name: string;
  url: string;
  file: File;  // Added to store the actual file object
}

// Use the imported type instead of local interface
type InvoiceDetails = InvoiceUploadData;

interface NotificationState {
  show: boolean;
  message: string;
  type: 'success' | 'error';
}

const WASTE_CATEGORIES = [
  { id: '1', name: 'General Waste' },
  { id: '2', name: 'Recyclable' },
  { id: '3', name: 'Hazardous' },
  { id: '4', name: 'Organic' },
];

export const PDFUpload: React.FC = () => {
  const [pdf, setPdf] = useState<PDFFile | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [notification, setNotification] = useState<NotificationState>({
    show: false,
    message: '',
    type: 'success',
  });
  const [invoiceDetails, setInvoiceDetails] = useState<InvoiceDetails>({
    invoiceNumber: '',
    invoiceDate: '',
    categoryId: '',
    categoryName: '',
    vendorName: 'Demo Vendor', // Default vendor
    vendorId: 'V12345' // Default vendor ID
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  const today = new Date();
  const minDate = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate()).toISOString().split('T')[0];
  const maxDate = new Date(today.getFullYear(), today.getMonth() + 1, today.getDate()).toISOString().split('T')[0];

  useEffect(() => {
    const savedData = localStorage.getItem('pdfUploadData');
    if (savedData) {
      const parsedData = JSON.parse(savedData);
      setInvoiceDetails(parsedData.invoiceDetails);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem('pdfUploadData', JSON.stringify({
      invoiceDetails,
      pdf: pdf ? { name: pdf.name, url: pdf.url } : null,
    }));
  }, [invoiceDetails, pdf]);

  const handleFileSelection = (file: File) => {
    if (file.type !== 'application/pdf') {
      showNotification('Please upload a PDF file', 'error');
      return;
    }
    
    setPdf({
      name: file.name,
      url: URL.createObjectURL(file),
      file: file
    });
  };

  const onFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    handleFileSelection(file);
  };

  const deletePDF = () => {
    if (pdf?.url) {
      URL.revokeObjectURL(pdf.url);
    }
    setPdf(null);
  };

  const resetForm = () => {
    deletePDF();
    setInvoiceDetails({
      invoiceNumber: '',
      invoiceDate: '',
      categoryId: '',
      categoryName: '',
      vendorName: 'Demo Vendor',
      vendorId: 'V12345'
    });
    localStorage.removeItem('pdfUploadData');
  };

  const onDragOver = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(true);
    event.dataTransfer.dropEffect = "copy";
  };

  const onDragLeave = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
  };

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
    
    const file = event.dataTransfer.files[0];
    if (!file) return;
    handleFileSelection(file);
  };

  const showNotification = (message: string, type: 'success' | 'error') => {
    setNotification({ show: true, message, type });
    setTimeout(() => {
      setNotification(prev => ({ ...prev, show: false }));
    }, 10000);
  };

  const handleSubmit = async () => {
    if (!pdf?.file || !invoiceDetails.invoiceNumber || !invoiceDetails.invoiceDate || !invoiceDetails.categoryId) {
      showNotification('Please fill all required fields', 'error');
      return;
    }

    setIsUploading(true);
    
    try {
      const formData = new FormData();
      formData.append('file_content', pdf.file);

      // Create file path
      const fileName = pdf.file.name.replace(/[^a-zA-Z0-9.-]/g, '_');
      const filePath = `invoices/${new Date().getFullYear()}/${fileName}`;

      // Build enhanced parameters for embedded document structure
      const uploadParams: InvoiceUploadParams = {
        vendor_name: invoiceDetails.vendorName || 'Demo Vendor',
        vendor_id: invoiceDetails.vendorId || 'V12345',
        category_id: invoiceDetails.categoryId,
        invoice_number: invoiceDetails.invoiceNumber,
        invoice_date: invoiceDetails.invoiceDate
      };

      // Use the enhanced API client
      const result = await api.invoices.upload(filePath, formData, uploadParams);
      
      showNotification('Success: Invoice uploaded with embedded vendor/category data', 'success');
      resetForm();
    } catch (error) {
      console.error('Upload error:', error);
      showNotification('Upload failed. Please try again.', 'error');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="card">
      {notification.show && (
        <Alert variant={notification.type}>
          <AlertDescription>{notification.message}</AlertDescription>
        </Alert>
      )}

      <div className="top">
        <p>PDF Upload</p>
      </div>

      <div className="mb-6 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Invoice Number
          </label>
          <input
            type="text"
            value={invoiceDetails.invoiceNumber}
            onChange={(e) => setInvoiceDetails(prev => ({ ...prev, invoiceNumber: e.target.value }))}
            className="w-full px-4 py-2 border rounded-lg focus:ring-blue-500 focus:border-blue-500"
            placeholder="Enter invoice number"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Invoice Date
          </label>
          <input
            type="date"
            value={invoiceDetails.invoiceDate}
            onChange={(e) => setInvoiceDetails(prev => ({ ...prev, invoiceDate: e.target.value }))}
            min={minDate}
            max={maxDate}
            className="w-full px-4 py-2 border rounded-lg focus:ring-blue-500 focus:border-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Waste Category
          </label>
          <select
            value={invoiceDetails.categoryId}
            onChange={(e) => {
              const selectedCategory = WASTE_CATEGORIES.find(cat => cat.id === e.target.value);
              setInvoiceDetails(prev => ({ 
                ...prev, 
                categoryId: e.target.value,
                categoryName: selectedCategory?.name || ''
              }));
            }}
            className="w-full px-4 py-2 border rounded-lg focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="">Select category</option>
            {WASTE_CATEGORIES.map(category => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      
      <div 
        className={`drag-area ${isDragging ? 'dragover' : ''}`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {isDragging ? (
          <span>Drop PDF here</span>
        ) : (
          <div className="upload-text">
            Drag & Drop PDF here or{" "}
            <label className="browse-button">
              Browse
              <input
                type="file"
                className="hidden"
                accept="application/pdf"
                ref={fileInputRef}
                onChange={onFileSelect}
              />
            </label>
          </div>
        )}
      </div>

      {pdf && (
        <div className="preview-section">
          <div className="flex justify-between items-center mb-4">
            <h2>Preview</h2>
            <button
              onClick={resetForm}
              className="flex items-center gap-2 text-gray-600 hover:text-gray-800"
            >
              <RefreshCw size={20} />
              <span>Reset</span>
            </button>
          </div>

          <div className="bg-gray-50 p-4 rounded-lg mb-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-500">Invoice Number</p>
                <p className="font-medium">{invoiceDetails.invoiceNumber || '-'}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Invoice Date</p>
                <p className="font-medium">{invoiceDetails.invoiceDate || '-'}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Category</p>
                <p className="font-medium">
                  {invoiceDetails.categoryName || '-'}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Filename</p>
                <p className="font-medium">{pdf.name}</p>
              </div>
            </div>
          </div>

          <div className="frame">
            <button
              onClick={deletePDF}
              className="delete-button"
            >
              <X size={20} />
            </button>
            <iframe
              src={pdf.url}
              className="w-full h-full"
              title="PDF Preview"
            />
          </div>
        </div>
      )}

      <button
        onClick={handleSubmit}
        className="mt-6 flex items-center justify-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
        disabled={isUploading || !pdf || !invoiceDetails.invoiceNumber || !invoiceDetails.invoiceDate || !invoiceDetails.categoryId}
      >
        {isUploading ? (
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white" />
        ) : (
          <Upload size={20} />
        )}
        <span>{isUploading ? 'Uploading...' : 'Upload Invoice'}</span>
      </button>
    </div>
  );
};

export default PDFUpload;