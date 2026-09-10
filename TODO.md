check drivetrain from api trim name as well as by auction sheet data


publish dashboard in github pages

# autowebdirect integration

bid reduced value rounded to thousands of yen I use to make bid in https://autowebdirect.com/. I take lot number, make and model name to fill search form and then press search button. Window with search result opens, and I click on the lot that match mentioned parameters.
In "MAKE A BID" form I fill "Your Bid:" as a "bid reduced" and chose group and num. We need to take group and num from toml search definition, by default use group "A" num "1".  

I think we need to start prototyping from authentication on the platform. I have login and password to authorise on the site. I tell me where to store them.




## WEB UI

Lets update our index page. The index page should contain a list of searches with dashboard enabled. after clicking search we see search page with all the information we have for the searches in order:
- link to index page
- upcoming and last 2 finished search runs with lots (everyday use block)
- competitors (I check it once a week)
- aiction_statistics (I check it once a week)

