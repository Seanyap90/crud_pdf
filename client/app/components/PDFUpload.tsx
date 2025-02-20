'use client';

import React, { useState, useRef, useEffect } from 'react';
import { X, Upload, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription } from './alert';

interface PDFFile {
  name: string;
  url: string;
}

interface InvoiceDetails {
  invoiceNumber: string;
  invoiceDate: string;
  category: string;
}

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
  // State management
  const [pdf, setPdf] = useState<PDFFile | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [notification, setNotification] = useState<NotificationState>({
    show: false,
    message: '',
    type: 'success',
  });
  const [invoiceDetails, setInvoiceDetails] = useState<InvoiceDetails>({
    invoiceNumber: '',
    invoiceDate: '',
    category: '',
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Calculate date range for date picker
  const today = new Date();
  const minDate = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate()).toISOString().split('T')[0];
  const maxDate = new Date(today.getFullYear(), today.getMonth() + 1, today.getDate()).toISOString().split('T')[0];

  // Load saved data from localStorage on component mount
  useEffect(() => {
    const savedData = localStorage.getItem('pdfUploadData');
    if (savedData) {
      const parsedData = JSON.parse(savedData);
      setInvoiceDetails(parsedData.invoiceDetails);
      if (parsedData.pdf) {
        setPdf(parsedData.pdf);
      }
    }
  }, []);

  // Save data to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem('pdfUploadData', JSON.stringify({
      invoiceDetails,
      pdf,
    }));
  }, [invoiceDetails, pdf]);

  const onFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    
    if (file.type !== 'application/pdf') {
      showNotification('Please upload a PDF file', 'error');
      return;
    }
    
    setPdf({
      name: file.name,
      url: URL.createObjectURL(file)
    });
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
      category: '',
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
    
    if (file.type !== 'application/pdf') {
      showNotification('Please upload a PDF file', 'error');
      return;
    }
    
    setPdf({
      name: file.name,
      url: URL.createObjectURL(file)
    });
  };

  const showNotification = (message: string, type: 'success' | 'error') => {
    setNotification({ show: true, message, type });
    setTimeout(() => {
      setNotification(prev => ({ ...prev, show: false }));
    }, 10000);
  };

  const handleSubmit = async () => {
    // Validate all fields are filled
    if (!pdf || !invoiceDetails.invoiceNumber || !invoiceDetails.invoiceDate || !invoiceDetails.category) {
      showNotification('Please fill all required fields', 'error');
      return;
    }

    // Mock API call data
    const mockData = {
      vendor_id: 'V123',
      vendor_name: 'Sample Vendor',
      invoice_id: 'INV' + invoiceDetails.invoiceNumber,
      filepath: `/uploads/${pdf.name}`,
      invoice_number: invoiceDetails.invoiceNumber,
      invoice_date: invoiceDetails.invoiceDate,
      category_id: invoiceDetails.category,
      filename: pdf.name
    };

    try {
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      showNotification('Success: Invoice uploaded', 'success');
      resetForm();
    } catch (error) {
      showNotification('Failure, please try again or contact administrator', 'error');
    }
  };

  return (
    <div className="card">
      {notification.show && (
        <Alert className={`mb-4 ${notification.type === 'success' ? 'bg-green-100 border-green-500' : 'bg-red-100 border-red-500'}`}>
          <AlertDescription className={notification.type === 'success' ? 'text-green-800' : 'text-red-800'}>
            {notification.message}
          </AlertDescription>
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
            value={invoiceDetails.category}
            onChange={(e) => setInvoiceDetails(prev => ({ ...prev, category: e.target.value }))}
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
                  {WASTE_CATEGORIES.find(c => c.id === invoiceDetails.category)?.name || '-'}
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
        className="mt-6 flex items-center justify-center gap-2"
        disabled={!pdf || !invoiceDetails.invoiceNumber || !invoiceDetails.invoiceDate || !invoiceDetails.category}
      >
        <Upload size={20} />
        <span>Upload Invoice</span>
      </button>
    </div>
  );
};

export default PDFUpload;