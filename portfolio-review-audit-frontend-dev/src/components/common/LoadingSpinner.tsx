import { Loader2 } from 'lucide-react';

const LoadingSpinner = () => {
  return (
    <div className="flex items-center justify-center h-full w-full min-h-[400px]">
      <Loader2 className="h-10 w-10 text-blue-500 animate-spin" />
      <span className="ml-3 text-lg font-medium text-gray-600">Loading...</span>
    </div>
  );
};

export default LoadingSpinner;