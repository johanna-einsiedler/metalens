// See https://observablehq.com/framework/config for documentation.
export default {
  // The project’s title; used in the sidebar and webpage titles.
  title: "Metalens",
 theme: 'air',
  home: `<img class="sidebar-logo" src="https://s6.imgcdn.dev/YB2KM0.png" alt="Metalens logo">`,

  // The pages and sections in the sidebar. If you don’t specify this option,
  // all pages will be listed in alphabetical order. Listing pages explicitly
  // lets you organize them into sections and have unlisted pages.
  pages: [
    { name: "Home", path: "/" },
    { name: "What is Metalens?", path: "/about" },
    {
      name: "Metastudies",
      open: true,
      pages: [
        {name: "Hydroxychloroquine", path: "/studies/dat.axfors2021"},
        {name: "Supervision Quality", path: "/studies/dat.aloe2013"},
        {name: "Conscientiousness & Medication adherence", path: "/studies/dat.molloy2014"},
        {name: "Writing-to-Learn Interventions", path: "/studies/dat.bangertdrowns2004"},
        //{name: "Conscientiousness & Medication adherence", path: "/studies/dat.bakdash2021"},
        //{name: "Conscientiousness & Medication adherence", path: "/studies/dat.assink2016"},
      ]
    },
    { name: "Upload data", path: "/input" },
    { name: "Methodology", path: "/methodology" },
    { name: "How to read the plots", path: "/eli5" },
    //{name: 'test', path: '/test'}
    //{name: "Report", path: "/example-report"}
  ],
  dynamicPaths: [
    "/studies/dat.axfors2021",
    "/studies/dat.aloe2013",
    "/studies/dat.molloy2014",
    "/studies/dat.bangertdrowns2004"
  ],

  // Some additional configuration options and their defaults:
  // theme: "default", // try "light", "dark", "slate", etc.
  head: `
    <script>
      (function () {
        const viewport = document.querySelector('meta[name="viewport"]');
        if (viewport) viewport.setAttribute("content", "width=device-width, initial-scale=1");
      })();
    </script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Martian+Mono:wght@100..800&family=Space+Grotesk:wght@300..700&display=swap" rel="stylesheet">
    <link rel="icon" href="data/images/logo-vat.png" type="image/png">
  `,
  // footer: "Built with Observable.", // what to show in the footer (HTML)
  // toc: true, // whether to show the table of contents
  //pager: true, // whether to show previous & next links in the footer
  root: "docs", // path to the source root for preview
  // output: "dist", // path to the output root for build
  search: {
    async *index() {
      yield {
        path: "/studies/dat.axfors2021",
        title: "Hydroxychloroquine",
        text: "Hydroxychloroquine and chloroquine survival in COVID-19 meta-study",
        keywords: "hydroxychloroquine, chloroquine, covid-19"
      };
      yield {
        path: "/studies/dat.aloe2013",
        title: "Supervision Quality",
        text: "Supervision Quality meta-study",
        keywords: "supervision"
      };
      yield {
        path: "/studies/dat.molloy2014",
        title: "Conscientiousness & Medication adherence",
        text: "Conscientiousness & Medication adherence meta-study",
        keywords: "conscientiousness, medication adherence"
      };
      yield {
        path: "/studies/dat.bangertdrowns2004",
        title: "Writing-to-Learn Interventions",
        text: "Writing-to-Learn Interventions meta-study",
        keywords: "writing to learn"
      };
    }
  }, // activate search
};
