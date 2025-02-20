'use client';

import React, { useState } from 'react';
import { Upload, ClipboardList } from 'lucide-react';
import { PDFUpload } from '../components/PDFUpload';
import { StatusList } from '../components/StatusList';

export const Layout: React.FC =() => {
  const [activeTab, setActiveTab] = useState('upload');


  return (
    <div className="layout-container">
      <div className="sidebar">
        <div className="sidebar-content">
          <h2 className="sidebar-title">Weight Invoice Manager</h2>
          <nav className="sidebar-nav">
            <button
              onClick={() => setActiveTab('upload')}
              className={`w-full flex items-center space-x-2 px-4 py-3 rounded-lg transition-colors ${
                activeTab === 'upload' 
                  ? 'bg-blue-500 text-white' 
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
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