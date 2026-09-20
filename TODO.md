check drivetrain from api trim name as well as by auction sheet data


publish dashboard in github pages


# Warn about unknown inspector notes.
Create database for all met notes and if new note appears, categorize it. there are two groups of the notes - problems that something don't work, missed etc and the second - just expected damages after usual expluatation of the car like scratches, soil. 
「ズレ」 (zure — «смещение / не по месту») написано от руки прямо над передним бампером на схеме кузова. Это и есть «смещен передний бампер» у экспортера. Больше про бампер на листе ничего нет:

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
- search params from toml

Q1 - ok
Q2 - ok
Q3 - ok
Q4 - ok
Q5 - future lots should be on the top of the search page because it is my everyday job to make bids, so I want to rich lots without extra clicks. But lots from the past should sit at another page available by link from search page. 
Q6 - ok
Q7 - see Q5. 

Q8 - ok. And we can use the same template to render passed lots.
Q9 - lots from last 7 days grouped by day with detailed view like report.html do it.
Q10 - ok
Q11 - (a) I have plans to publish my dashboard to girhub pages. May be self-contained pages will simplify making it.
Q12 - ok
Q13 - I am not sure about Run term. I don't expect to have such entity in my dashboard. It is just one step in my flow when I want to find lots for upcoming auctions.
Q14 - ok


Q15 - ok. We don't need to reach older runs at all. It is ok to keep theirs data in database, but we need nothing in the UI.
Q16 - (a). We will optimize page size in future if it starts bother me
Q17 - (a) out of scope
Q18 - ok. The main requirement for the Heading - It should be clear for what day are lots found. Or were not found. 

Q19 - ok
Q20 - ok
Q21 - No, don't show.
Q22 - Index page says nothing about upcoming day. Index page contains only sorted searches.
Q23 - (b) keep opening the index;

Q24 - Sorry, I didn't understand Q22 correctly. It is ok to display summary only about the count of upcoming lots to bid. But we don't need words about competing ads or auction stats.
Q25 - a
Q26 - a
Q27 - b. don't delete from disk

Q28 - a, Q29 - ok, Q30 - c. , Q31 - ok