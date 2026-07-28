import { Link } from 'react-router-dom';

export default function Home() {
  return (
    <div className="max-w-xl">
      <h1 className="text-2xl font-bold text-gray-900">In Review Tracker</h1>
      <p className="mt-2 text-gray-600">You are signed in. Add new routes under <code className="text-sm bg-gray-100 px-1 rounded">src/pages</code>.</p>
      <p className="mt-4">
        <Link to="/dummy/test-page" className="text-blue-600 hover:text-blue-800 font-medium">
          Open dummy test page →
        </Link>
      </p>
    </div>
  );
}
