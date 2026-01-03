import { Search, BookOpen, Upload, Globe, MessageCircle, Settings, ChevronDown, Layers } from 'lucide-react';
import { useState } from 'react';

export default function Sidebar() {
  const [isMetaStudyOpen, setIsMetaStudyOpen] = useState(true);

  return (
    <aside className="w-80 bg-white h-screen flex flex-col border-r border-gray-200">
      <div className="p-6">
        <div className="flex items-center gap-2 mb-6">
          <Layers className="w-5 h-5 text-teal-600" />
          <h1 className="text-xl font-semibold text-gray-800 tracking-wide">METALENS</h1>
        </div>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search"
            className="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
          />
        </div>
      </div>

      <nav className="flex-1 px-3">
        <button className="w-full flex items-center gap-3 px-3 py-2.5 text-gray-700 hover:bg-gray-50 rounded-lg text-sm">
          <BookOpen className="w-4 h-4" />
          <span>What is Metalens?</span>
        </button>

        <div className="mt-1">
          <button
            onClick={() => setIsMetaStudyOpen(!isMetaStudyOpen)}
            className="w-full flex items-center justify-between px-3 py-2.5 bg-[#d9e5d6] rounded-lg text-sm font-medium text-gray-800"
          >
            <span>Meta Study 1</span>
            <ChevronDown className={`w-4 h-4 transition-transform ${isMetaStudyOpen ? '' : '-rotate-90'}`} />
          </button>

          {isMetaStudyOpen && (
            <div className="bg-[#d9e5d6] rounded-b-lg pb-2 -mt-1">
              <button className="w-full px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-[#ccd9c4] rounded-lg ml-3 mr-3">
                Meta Study 2
              </button>
              <button className="w-full px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-[#ccd9c4] rounded-lg ml-3 mr-3">
                Meta Study 3
              </button>
              <button className="w-full px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-[#ccd9c4] rounded-lg ml-3 mr-3">
                Meta Study 4
              </button>
            </div>
          )}
        </div>

        <button className="w-full flex items-center gap-3 px-3 py-2.5 text-gray-700 hover:bg-gray-50 rounded-lg text-sm mt-1">
          <Upload className="w-4 h-4" />
          <span>Upload Data</span>
        </button>

        <button className="w-full flex items-center gap-3 px-3 py-2.5 text-gray-700 hover:bg-gray-50 rounded-lg text-sm mt-1">
          <Globe className="w-4 h-4" />
          <span>Writing-to-Learn Interventions</span>
        </button>

        <button className="w-full flex items-center gap-3 px-3 py-2.5 text-gray-700 hover:bg-gray-50 rounded-lg text-sm mt-1">
          <MessageCircle className="w-4 h-4" />
          <span>About</span>
        </button>
      </nav>

      <div className="p-4 border-t border-gray-200">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 bg-gradient-to-br from-teal-400 to-blue-500 rounded-full flex items-center justify-center text-white font-semibold">
            J
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-gray-800">Johanna</p>
            <span className="inline-block px-2 py-0.5 bg-teal-600 text-white text-xs rounded-full">Admin</span>
          </div>
        </div>
        <button className="w-full flex items-center gap-3 px-3 py-2 text-gray-700 hover:bg-gray-50 rounded-lg text-sm">
          <Settings className="w-4 h-4" />
          <span>Settings</span>
        </button>
      </div>
    </aside>
  );
}
