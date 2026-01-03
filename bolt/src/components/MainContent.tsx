import NetworkBackground from './NetworkBackground';

export default function MainContent() {
  return (
    <main className="flex-1 overflow-y-auto bg-gray-50">
      <div className="relative h-[500px] bg-gradient-to-br from-blue-100 via-blue-50 to-white overflow-hidden">
        <NetworkBackground />
        <div className="relative z-10 h-full flex items-center justify-center px-12">
          <h1 className="text-4xl font-bold text-gray-900 text-center max-w-5xl leading-tight">
            Mortality Outcomes with Hydroxychloroquine and Chloroquine in COVID-19 from an International Collaborative Meta-Analysis of Randomized Trials
          </h1>
        </div>
      </div>

      <div className="px-12 py-8">
        <div className="max-w-6xl">
          <div className="bg-[#f5f5e8] rounded-lg p-8 shadow-sm">
            <h2 className="text-lg font-bold text-gray-900 mb-4 uppercase tracking-wide">Summary:</h2>

            <p className="text-gray-800 mb-4 leading-relaxed">
              Results from 33 trials examining the effectiveness of hydroxychloroquine or chloroquine in patients with COVID-19.
            </p>

            <p className="text-gray-800 leading-relaxed">
              The dataset includes the results from 33 published and unpublished randomized clinical trials that examined the effectiveness of hydroxychloroquine or chloroquine in patients with COVID-19. The results given here are focused on the total mortality in the treatment versus control groups.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
