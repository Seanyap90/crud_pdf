'use client';

import React, { useState, useEffect } from 'react';
import { Info, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription } from './alert';
import { api } from '../../lib/api_client';
import type { Invoice, InvoiceListResponse } from '../../types/invoice';

const API_BASE_URL = 'http://localhost:8000';

// Mock vendor data - in a real app this would come from auth context
const MOCK_VENDOR = {
  name: 'Demo Vendor',
  id: 'V12345'
};

export const StatusList: React.FC = () => {
  const [data, setData] = useState<Invoice[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchInvoices = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      console.log('Fetching invoices with embedded objects for vendor:', MOCK_VENDOR.id);
      
      // Use the enhanced API client that handles embedded documents
      const result: InvoiceListResponse = await api.invoices.getList(MOCK_VENDOR.id);
      console.log('Received invoices with embedded objects:', result);
      
      setData(result.invoices);
      setTotalCount(result.total_count);
    } catch (error) {
      console.error('Error fetching invoices:', error);
      setError('Failed to load invoices. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchInvoices();
    
    // Set up smart polling - only poll when there are processing invoices
    const pollInterval = setInterval(() => {
      // Only poll if there are invoices in processing or pending state
      const hasActiveInvoices = data.some(invoice => 
        invoice.extraction_status === 'processing' || invoice.extraction_status === 'pending'
      );
      
      if (hasActiveInvoices) {
        fetchInvoices();
      }
    }, 2000); // Poll every 2 seconds when needed
    
    // Cleanup interval on component unmount
    return () => clearInterval(pollInterval);
  }, []); // Run only once on component mount

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'text-green-500';
      case 'processing':
        return 'text-yellow-500';
      case 'pending':
        return 'text-blue-500';
      case 'failed':
        return 'text-red-500';
      default:
        return 'text-gray-500';
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const formatNumber = (value: any): string => {
    if (value === null || value === undefined) return '-';
    const num = typeof value === 'number' ? value : parseFloat(value.toString());
    return isNaN(num) ? '-' : num.toFixed(2);
  };

  if (error) {
    return (
      <Alert variant="error">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="status-container">
      <div className="vendor-header mb-6">
        <h1 className="text-2xl font-bold text-gray-900">{MOCK_VENDOR.name}</h1>
        <p className="text-sm text-gray-600">Vendor ID: {MOCK_VENDOR.id}</p>
      </div>

      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold text-gray-800">Invoice Status</h2>
        <button
          onClick={fetchInvoices}
          className={`flex items-center space-x-2 px-4 py-2 rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition-colors ${
            isLoading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
          disabled={isLoading}
        >
          <RefreshCw className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center items-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Invoice ID
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Invoice Number
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Vendor
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Category
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Filename
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Weight (kg)
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Total Price
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Upload Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {data.map((item) => (
                <tr key={item.invoice_id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {item.invoice_id}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {item.invoice_number}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <div className="flex flex-col">
                      <span className="font-medium text-gray-900">{item.vendor.vendor_name}</span>
                      <span className="text-xs text-gray-500">{item.vendor.vendor_id}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    {item.category ? (
                      <span className="px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                        {item.category.category_name}
                      </span>
                    ) : (
                      <span className="text-gray-400">No category</span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {item.filename}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {formatNumber(item.reported_weight_kg)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {formatNumber(item.total_amount)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {formatDate(item.upload_date)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <span className={getStatusColor(item.extraction_status)}>
                      {item.extraction_status.charAt(0).toUpperCase() + item.extraction_status.slice(1)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 text-sm text-gray-500">
        Total Invoices: {totalCount}
      </div>
    </div>
  );
};

export default StatusList;