'use client';

import React, { useState, useEffect } from 'react';
import { Info, RefreshCw } from 'lucide-react';

// Mock categories mapping (in real app, this would come from API)
const MATERIAL_CATEGORIES = {
  1: 'Paper',
  2: 'Plastic',
  3: 'Metal',
  4: 'Glass',
  5: 'Organic'
};

// Mock data structure matching the database schema
const mockData = Array.from({ length: 25 }, (_, i) => ({
  invoice_id: i + 1,
  invoice_number: `INV-2024-${String(i + 1).padStart(4, '0')}`,
  filename: `invoice_${i + 1}.pdf`,
  category_id: Math.floor(Math.random() * 5) + 1, // Random category between 1-5
  reported_weight_kg: Math.floor(Math.random() * 1000 * 100) / 100,
  total_amount: Math.floor(Math.random() * 10000 * 100) / 100,
  upload_date: new Date(Date.now() - Math.random() * 10000000000).toISOString(),
  extraction_status: ['pending', 'processing', 'completed', 'failed'][Math.floor(Math.random() * 4)]
}));

const VENDOR_NAME = "ABC Trading Company"; // This would come from auth context or API
const VENDOR_ID = "V12345"; // This would come from auth context or API

export const StatusList: React.FC = () => {
  const [data, setData] = useState(mockData);
  const [currentPage, setCurrentPage] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const itemsPerPage = 10;

  const refreshData = async () => {
    setIsLoading(true);
    try {
      // Replace with actual API call
      // const response = await fetch(`/api/invoices?vendor_id=${VENDOR_ID}`);
      // const data = await response.json();
      // setData(data);
      await new Promise(resolve => setTimeout(resolve, 1000));
      setData([...mockData].sort(() => Math.random() - 0.5));
    } catch (error) {
      console.error('Failed to fetch invoice data:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    refreshData();
  }, []);

  const totalPages = Math.ceil(data.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentData = data.slice(startIndex, startIndex + itemsPerPage);

  const getStatusColor = (status) => {
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

  const getCategoryColor = (categoryId) => {
    switch (categoryId) {
      case 1: // Paper
        return 'bg-yellow-100 text-yellow-800';
      case 2: // Plastic
        return 'bg-blue-100 text-blue-800';
      case 3: // Metal
        return 'bg-gray-100 text-gray-800';
      case 4: // Glass
        return 'bg-green-100 text-green-800';
      case 5: // Organic
        return 'bg-brown-100 text-brown-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  return (
    <div className="status-container">
      <div className="vendor-header">
        <h1 className="vendor-name">{VENDOR_NAME}</h1>
        <p className="vendor-id">Vendor ID: {VENDOR_ID}</p>
      </div>

      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-bold text-gray-800">Invoice Status</h2>
        <button
          onClick={refreshData}
          className={`flex items-center space-x-2 px-4 py-2 rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition-colors ${
            isLoading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
          disabled={isLoading}
        >
          <RefreshCw className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

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
                Extraction Status
              </th>
              <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                Info
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {currentData.map((item) => (
              <tr key={item.invoice_id} className="hover:bg-gray-50">
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  {item.invoice_id}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  {item.invoice_number}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${getCategoryColor(item.category_id)}`}>
                    {MATERIAL_CATEGORIES[item.category_id]}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {item.filename}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  {item.reported_weight_kg?.toFixed(2) || '-'}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  ${item.total_amount?.toFixed(2) || '-'}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {formatDate(item.upload_date)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm">
                  <span className={getStatusColor(item.extraction_status)}>
                    {item.extraction_status.charAt(0).toUpperCase() + item.extraction_status.slice(1)}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  <div className="flex justify-center">
                    <button className="text-blue-500 hover:text-blue-600">
                      <Info size={20} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex justify-between items-center mt-6">
        <div className="text-sm text-gray-500">
          Showing {startIndex + 1} to {Math.min(startIndex + itemsPerPage, data.length)} of {data.length} entries
        </div>
        <div className="flex space-x-2">
          <button
            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            disabled={currentPage === 1}
            className="px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <button
            onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
            disabled={currentPage === totalPages}
            className="px-4 py-2 border rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
};

export default StatusList;