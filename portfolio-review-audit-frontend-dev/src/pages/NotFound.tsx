import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

export default function NotFound() {
  const location = useLocation();

  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('404 Error: User attempted to access non-existent route:', location.pathname);
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="mb-4 text-4xl font-bold text-gray-900">404</h1>
        <p className="mb-4 text-xl text-gray-500">Oops! Page not found</p>
        <a href="/" className="text-blue-600 underline hover:text-blue-800">
          Return to Home
        </a>
      </div>
    </div>
  );
}

