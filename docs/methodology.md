

<link
  rel="stylesheet"
  href="https://stackpath.bootstrapcdn.com/bootstrap/4.1.3/css/bootstrap.min.css"
  integrity="sha384-MCw98/SFnGE8fJT3GXwEOngsV7Zt27NXFoaoApmYm81iuXoPkFOJwJ8ERdknLPMO"
  crossorigin="anonymous"
/><link rel="stylesheet" href="styles/styles.css">
<style>
    #observablehq-main,
#observablehq-header,
#observablehq-footer 
{
  max-width: 700px;
}
</style>


# Methodology

## Aggregation of results

We use two different approaches to aggregate the results from the different studies included in the meta-analysis: a fixed effects model and a random effects model.

### Fixed effects model

A so-called 'fixed effects model' is probably the simplest version of calculating meta-estimates. It uses the 'inverse-variance method' to calculate the weighted average of the included studies as:


A so-called 'fixed effects model' is probably the simplest version of calculating meta-estimates. It uses the 'inverse-variance method' to calculate the weighted average of the included studies as: 
```tex 
\text{meta-estimate} = \frac{\sum{Y_iW_i}}{\sum{W_i}} 
```

where ${tex`Y_i`} is the estimated effect size from the ${tex`i^{th}`} study, ${tex`W_i`} is the weight assigend to study ${tex`i`} and the summation is across all studies.
In the fixed effects model, 
```tex 
W_i =\frac{1}{SE_i^2}
```
with ${tex`SE_i`} being the standard error of study ${tex`i`}.

This type of analysis assumes that all effect estimates are estimating the same underlying effect.
More information: Borenstein, M.; Hedges, L.; Higgins, J.; Rothstein, H (2009): <a href="https://www.meta-analysis.com/downloads/Intro_Models.pdf">A basic introduction to fixed-effect and
    random-effects models for meta-analysis</a>


### Random effects model
 Random effects models are a variation of the 'inverse-variance method' that is also used in fixed effects models. However, they operate on the assumption that the different studies included in the meta-analysis estimate different but related effects.

The general formula for calculating the aggregated effect estimate is the same as in the fixed effects model:
```tex 
\text{meta-estimate} = \frac{\sum{Y_iW_i}}{\sum{W_i}} 
```
where ${tex`Y_i`}  is the estimated effect size from the ${tex`i^{th}`} study, ${tex`W_i`} is the weight assigend to study ${tex` i`} and the summation is across all studies. 

However, the weights of the studies take into account the between-study variance ${tex` T^2 `}:

```tex
W_i =\frac{1}{(T^2 + SE_i^2)}
```
You can find information on how to calculate \(T^2 \) in: Borenstein, M.; Hedges, L.; Higgins, J.; Rothstein, H (2009): <a href="https://www.meta-analysis.com/downloads/Intro_Models.pdf">A basic introduction to fixed-effect and
    random-effects models for meta-analysis</a>


