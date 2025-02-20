import { ReactNode } from 'react';

interface AlertProps {
  children: ReactNode;
  className?: string;
  variant?: 'success' | 'error';
}

export const Alert: React.FC<AlertProps> = ({ 
  children, 
  className = '', 
  variant = 'success' 
}) => {
  const baseClasses = 'alert';
  const variantClasses = variant === 'success' ? 'alert-success' : 'alert-error';
  
  return (
    <div role="alert" className={`${baseClasses} ${variantClasses} ${className}`}>
      {children}
    </div>
  );
};

interface AlertDescriptionProps {
  children: ReactNode;
  className?: string;
}

export const AlertDescription: React.FC<AlertDescriptionProps> = ({ 
  children, 
  className = '' 
}) => {
  return (
    <div className={`alert-description ${className}`}>
      {children}
    </div>
  );
};