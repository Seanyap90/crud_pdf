'use client';

import React, { useState } from 'react';
import { Upload, ClipboardList, Loader2 } from 'lucide-react';
import { PDFUpload } from './PDFUpload';
import { StatusList } from './StatusList';
import { useApiReady } from '../../lib/api_client';

export const Layout: React.FC = () => {
  const [activeTab, setActiveTab] = useState('upload');
  const { isReady, isChecking, error } = useApiReady();

  // Show loading while checking API availability
  if (isChecking) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-50">
        <div className="animate-spin mb-4">
          <Loader2 size={48} className="text-blue-500" />
        </div>
        <p className="text-lg font-medium text-gray-700">Connecting to backend services...</p>
        <p className="text-sm text-gray-500 mt-2">The server is starting up, please wait a moment</p>
      </div>
    );
  }

  // Show error if API is not available
  if (!isReady && !isChecking) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-50">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <h2 className="text-red-600 text-xl font-bold mb-2">Backend Connection Error</h2>
          <p className="text-gray-700 mb-4">
            Unable to connect to the backend API. Please ensure the FastAPI server is running.
          </p>
          {error && (
            <p className="text-gray-500 text-sm">
              Error details: {error}
            </p>
          )}
          <div className="mt-6 text-sm text-gray-600 bg-gray-100 p-3 rounded border border-gray-200">
            <p className="font-medium mb-1">Troubleshooting:</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>Make sure the FastAPI server is running with <code className="bg-gray-200 px-1 rounded">make local-mock</code></li>
              <li>Check that models have finished loading</li>
              <li>Verify the API is accessible at <code className="bg-gray-200 px-1 rounded">http://localhost:8000</code></li>
            </ol>
          </div>
          <button 
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  // If API is ready, show the main layout
  return (
    <div className="layout-container">
      <div className="sidebar">
        <div className="sidebar-content">
          <h2 className="sidebar-title">Weight Invoice Manager</h2>
          <div className="mb-3 px-2 py-1 text-xs bg-green-50 text-green-600 rounded-md border border-green-200">
            API Connected
          </div>
          <nav className="sidebar-nav">
            <button
              onClick={() => setActiveTab('upload')}
              className={`w-full flex items-center space-x-2 px-4 py-3 rounded-lg transition-colors ${
                activeTab === 'upload' 
                  ? 'bg-blue-500 text-white' 
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
              type="button"
            >
              <Upload size={20} />
              <span>Upload Files</span>
            </button>
            <button
              onClick={() => setActiveTab('status')}
              className={`w-full flex items-center space-x-2 px-4 py-3 rounded-lg transition-colors ${
                activeTab === 'status' 
                  ? 'bg-blue-500 text-white' 
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
              type="button"
            >
              <ClipboardList size={20} />
              <span>Status Review</span>
            </button>
          </nav>
        </div>
      </div>

      {/* Main Content */}
      <div className="main-content">
        <div className="content-container">
          {activeTab === 'upload' ? <PDFUpload /> : <StatusList />}
        </div>
      </div>
    </div>
  );
};

export default Layout;