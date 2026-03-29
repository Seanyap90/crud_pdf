'use client';

import { BarChart2, Router } from 'lucide-react';

export default function AnalyticsPage() {
  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar - similar to the one in gateway-dashboard.tsx */}
      <div className="w-64 bg-white shadow-lg">
        <div className="p-6">
          <h2 className="text-xl font-bold text-blue-500 mb-6">IoT Gateway Manager</h2>
          <div className="mb-3 px-2 py-1 text-xs bg-green-50 text-green-600 rounded-md border border-green-200">
            System Online
          </div>
          <nav className="space-y-2">
            <a
              href="/"
              className="w-full flex items-center space-x-2 px-4 py-3 rounded-lg text-gray-600 hover:bg-gray-100"
            >
              <Router size={20} />
              <span>Gateways</span>
            </a>
            <div
              className="w-full flex items-center space-x-2 px-4 py-3 rounded-lg bg-blue-500 text-white"
            >
              <BarChart2 size={20} />
              <span>Analytics</span>
            </div>
          </nav>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        <div className="p-8">
          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="mb-6">
              <h1 className="text-2xl font-bold text-gray-900">Analytics Dashboard</h1>
              <p className="text-sm text-gray-600">Gateway performance and metrics</p>
            </div>
            
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-gray-800">Performance Overview</h2>
            </div>
            
            <div className="flex items-center justify-center h-64 bg-gray-50 rounded-lg border border-gray-200">
              <div className="text-center">
                <BarChart2 size={48} className="mx-auto text-gray-400 mb-4" />
                <p className="text-gray-500">Analytics features coming soon</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}