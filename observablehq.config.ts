// See https://observablehq.com/framework/config for documentation.
export default {
  // The project’s title; used in the sidebar and webpage titles.
  title: "Metalens",
 theme: 'air',

  // The pages and sections in the sidebar. If you don’t specify this option,
  // all pages will be listed in alphabetical order. Listing pages explicitly
  // lets you organize them into sections and have unlisted pages.
  pages: [
  {
    name: "Examples",
    pages: [
      {name: "Hydroxchloroquine", path: '/studies/dat.axfors2021'},
      {name: "Supervision Quality", path: "/studies/dat.aloe2013"},
        {name: "Conscientiousness & Medication adherence", path: "studies/dat.molloy2014"},
        //{name: "Writing-to-Learn Interventions", path: "studies/dat.bangertdrowns2004"},
        //{name: "Conscientiousness & Medication adherence", path: "studies/dat.bakdash2021"},
        //{name: "Conscientiousness & Medication adherence", path: "studies/dat.assink2016"},

    ]},
    {name: "Upload your own data",
    pages: [
      {name: "Input", path: "/input"}
    ]},

    {name: "About",
      pages: [
        {name: "About the project", path: "/about"},
        {name: "Methodology", path:"/methodology"}
      ]},




        //{name: 'test', path: '/test'}
 //       {name: "Report", path: "/example-report"}
    
 ],
  dynamicPaths: [
        "/studies/dat.axfors2021",
    "/studies/dat.aloe2013",
    "/studies/dat.molloy2014",
    "/studies/dat.bangertdrowns2004",
    "/studies/dat.bakdash2021",
    "/studies/dat.assink2016"



  ],

  // Some additional configuration options and their defaults:
  // theme: "default", // try "light", "dark", "slate", etc.
  // header: "", // what to show in the header (HTML)
  // footer: "Built with Observable.", // what to show in the footer (HTML)
  // toc: true, // whether to show the table of contents
  //pager: true, // whether to show previous & next links in the footer
  // root: "docs", // path to the source root for preview
  // output: "dist", // path to the output root for build
  search: true, // activate search
};


