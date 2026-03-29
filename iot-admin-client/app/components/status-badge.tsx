import type { GatewayStatusType, GatewayStateType } from "../../shared/schema";

interface StatusBadgeProps {
  status: GatewayStatusType | GatewayStateType | string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  // Get the appropriate CSS classes based on status
  const getStatusClasses = () => {
    const baseClass = "px-2 py-1 rounded-full text-xs font-medium inline-block";
    
    switch (status.toLowerCase()) {
      // Backend state types (from state machine)
      case 'connected':
        return `${baseClass} bg-green-100 text-green-800`;
      case 'disconnected':
        return `${baseClass} bg-yellow-100 text-yellow-800`;
      case 'created':
        return `${baseClass} bg-blue-100 text-blue-800`;
      case 'deleted':
        return `${baseClass} bg-gray-100 text-gray-800`;
        
      // Classic status types
      case 'online':
        return `${baseClass} bg-green-100 text-green-800`;
      case 'offline':
        return `${baseClass} bg-yellow-100 text-yellow-800`;
      case 'warning':
        return `${baseClass} bg-yellow-100 text-yellow-800`;
      case 'error':
        return `${baseClass} bg-red-100 text-red-800`;
      
      default:
        return `${baseClass} bg-gray-100 text-gray-800`;
    }
  };
  
  // Make status more readable for display
  const getDisplayText = () => {
    switch (status.toLowerCase()) {
      case 'connected':
        return 'Connected';
      case 'disconnected':
        return 'Disconnected';
      case 'created':
        return 'Created';
      case 'deleted':
        return 'Deleted';
      case 'online':
        return 'Online';
      case 'offline':
        return 'Offline';
      case 'warning':
        return 'Warning';
      case 'error':
        return 'Error';
      default:
        // Capitalize first letter for other statuses
        return status.charAt(0).toUpperCase() + status.slice(1);
    }
  };
  
  return (
    <span className={getStatusClasses()}>
      {getDisplayText()}
    </span>
  );
}
