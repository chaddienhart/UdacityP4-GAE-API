## Udacity Full Stack Web Developer Project 

# Conference Central - [App Engine][1] APIs implemented using[Python][2]

# APIs added in this project 
getConferenceSessions()
getSessionsByType()
getSessionsBySpeaker()
createSession()
addSessionToWishlist()
getSessionsInWishlist()
getConferenceByTopic()
getSessionByCity()
getFeaturedSpeaker()

For the full api see:
https://udacity-p4-chaddienhart.appspot.com/_ah/api/explorer

# Task 1 - Add Sessions to a Conference
The Session entity stores the name, highlights, speaker, durationHours, type, date, startTime, 
and web safe conference key of the conference it was created under. 
The session key is auto-generated.
In my implementation a Session is an kind and the speaker is just a string property of the Session. 
I chose to store the web safe conference key instead of storing the session keys in the conference
because for queries across all sessions can be implemented by filtering sessions. Also finding all
sessions in a conference is just another filter. Note: that only the conference originator can 
create sessions.

# Task 2 - Add Sessions to User Wishlist

Wishlist sessions are stored in the Profile entity under sessionKeysWishList as a list of web safe keys.

# Task 3 - Work on indexes and queries
query 1: Get conferences by topic, which is useful since I'm willing to travel to a city with a conference on topics I'm interested in.
query 2: Get sessions in a city, this is used if you are in a city with multiple conferences you can browse all the sessions at one time.

Solve the following query related problem:
Let’s say that you don't like workshops and you don't like sessions after 7 pm. How would you handle a query for all non-workshop sessions before 7 pm? What is the problem for implementing this query? What ways to solve it did you think of?

The problem with this query is that you are only allowed one inequality filter and this query would naturally have two (time < 19 and sessionType != workshop).
You could filter the sessionType to be equal to any of the other types using an OR, but I'm not sure if that works.
I would prefer to do it this way:
You could get the results, as a list of sessionKeys, of the two inequality filters and intersect the two lists.
final list = set1.intersection(set2) 

# Task 4 
Add a task to set a featured speaker for a conference. A speaker is set as the featured speaker if when a session is added
they have more than one session at the conference. The speaker, and all of the session names are stored in memcache and can 
be recalled using the getFeaturedSpeaker() endpoint.

[1]: https://developers.google.com/appengine
[2]: http://python.org
