To decide about max_bid_jpy price in toml search description like  @searches/toyota-rav4.toml I need to check auction statistics

action stat is available at - https://banzai24.com/TOYOTA/RAV4?yearStart=2023&yearEnd=2023&mileageEnd=50000&engineCapacityStart=2.5&modelGrade=HYBRID+G&gradeOrigin=4&gradeOrigin=4.5&gradeOrigin=5&status=SOLD&source=archive&countryISO=JP

Above the filters in [site] block we need to filter stat by "body_model_code" and Модификация (modelGrade) like "HYBRID G". I will declare these extra filters in a new auction_statistics block

the result I want to see in dashboard like competitors page is made. Stats need for each enabled search.

We need to check if there is api exists for an auction statistics in banzai24 to decide what kind of output we are able to make.
- It will be nice to have prices for all sales  in last three month and make histogramm to see count of sales. In this case we apply api filters only, without auction sheet ai inspection
We need 5 lots that meets requirements from [sheet] block with the cheapest sale price. It means we need ai inspection for some lots.

I need to update stat every week, and we don't need to inspect known lots with ai twice.