
I want to introduce dashboard as a web page to see the current state of my job. The job is to choose car that is cheaper in Japan than in Cyprus.

The first panel in the dashboard is the list of competitors for the enabled searches like banzai24/searches/mazda-cx30.toml
competitors - advertisements from bazaraki with similar properties but with the lower price than the sell price for the search.
Sell price - is the expected land price plus profit I expect from this car. 
So we need to add expected profit to search definition (banzai24/searches/mazda-cx30.toml). Add enabled property for this search. Because I want to see competitors not for all searches in banzai24/searches directory.

The link to dashboard I want to see from index page @runs/index.html. The index page we also need to move to dashboard module. This module - is the user interface for my workflow.

add cli command to run browser with dashboard with the same profile we make requests to banzai24

Q1 - cyprus_sell_price = landed cost + resale costs + expected profit
Q2 - a
Q3 -  same make/model. And define all other fields for competitors explicitly in search parameters under [competitors] block. Because we consider car with bigger milage also as competitor. as well as black cars. Older car is also competitor
Q4 - A new [dashboard] section: enabled = true, expected_profit_eur = 2000. Absent section ⇒ enabled = true.  fetch --search mazda-3 keeps working on a disabled search.
Q5 - see Q3
Q6 - Static. dashboard build rewrites the HTML; there's no live data behind it that a server would help with 
  (the DBs only change when you run a scrape), and a server is a process to remember to kill. It also keeps  
  session.review() — which needs a file:// URI — working unchanged.         
Q7 - dashboard/ owns the rendering code and imports both parsers; neither parser imports dashboard. banzai24 report stops rewriting the index; dashboard build writes both runs/index.html and runs/competitors.html (side by side so links work in both directions), and daily.sh calls dashboard build as its last step. Cost: the index is stale between a manual report and the next dashboard build — acceptable, since the index is derived from directory names and rebuilding is free.

Q8 - lets merge banzai24/inputs/bid_prices.csv with toml searches. So one search will define make/model and a list of blocks for different milages / years / competitors params

Q9 - Yes. lets merge bazaraki search definition into common toml file. Move this toml to new module - 'searches'

Q12 - c
Q13 - Derived. But we need to define [competitors] common parameters to apply in bazaraki search like fuel / engine size

Q14 -  Keep per-band competitor bounds in the file — they're expressive and cost nothing — but one scrape per  
  search, covering the union of its bands' competitor bounds, one ScrapeRun, one clean delisting scope. Each 
  band then filters the already-scraped rows by its own bounds in memory. Same expressiveness, one crawl, and
  delisting stays the simple thing it is today.          
Q15 - agree
Q16 - agree
Q17 - agree

Q18 - ok
Q19 - ok
Q20 - ok
Q21 - ok
Q22 - ok

Q23 - ok
Q24 - ok
Q25 - ok
Q26 - ok

Q27 - ok
Q28 - create abstract class with definitions of the model and make for the existing searches. I don't want to know about implementation details of the parsers.