To decide about max_bid price I need to check auction statistics for the model in search description like  @searches/toyota-rav4.toml

URL example for action stat - https://banzai24.com/TOYOTA/RAV4?yearStart=2023&yearEnd=2023&mileageEnd=50000&engineCapacityStart=2.5&modelGrade=HYBRID+G&gradeOrigin=4&gradeOrigin=4.5&gradeOrigin=5&status=SOLD&source=archive&countryISO=JP

Above the filters in [site] block we need to filter stat by "body_model_code" and Модификация (modelGrade) like "HYBRID G"

the result I want to see in dashboard like competitors page is made. Stats need for each enabled search.

We need to check if there is api exists for a auction statistics in banzai24 to decide what kind of output we are able to make.
It will be good if we can get prices for all sales in last three month and make histogramm to see count of sales.
We need 