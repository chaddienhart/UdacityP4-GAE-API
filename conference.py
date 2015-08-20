#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import logging
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.ext import ndb

from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID


from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms

from models import Session
from models import SessionForm
from models import SessionForms

from models import BooleanMessage
from models import ConflictException

from google.appengine.api import memcache
from models import StringMessage
from google.appengine.api import taskqueue

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_BY_TYPE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2)
)

SESSION_BY_SPEAKER = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

SESSION_ADD_WISH_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

CONF_BY_TOPIC = endpoints.ResourceContainer(
    message_types.VoidMessage,
    topic=messages.StringField(1),
)

SESSION_BY_CITY = endpoints.ResourceContainer(
    message_types.VoidMessage,
    city=messages.StringField(1),
)



DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

SESSION_DEFAULTS = {
    "highlights": ["tbd"],
    "speaker": "guest speaker",
    "durationHours": 1.0,
    "type": "lecture",
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Conference objects - - - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(), 'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email')

        return request

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                path='queryConferences',
                http_method='POST',
                name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

         # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") \
            for conf in conferences]
        )

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # make profile key
        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for this user
        conferences = Conference.query(ancestor=p_key)
        # get the user profile and display name
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms, path='filterPlayground',
                      http_method='POST', name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.order(Conference)
        q = q.filter(Conference.maxAttendees > 10)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q])

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        # TODO:
        # step 1: get user profile
        # step 2: get conferenceKeysToAttend from profile.
        # to make a ndb key from websafe key you can use:
        # ndb.Key(urlsafe=my_websafe_key_string)
        # step 3: fetch conferences from datastore.
        # Use get_multi(array_of_keys) to fetch all keys at once.
        # Do not fetch them one by one!
        # return set of ConferenceForm objects per Conference
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        return ConferenceForms(items=[self._copyConferenceToForm(conf, "") for conf in conferences])

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ''
        return StringMessage(data=announcement)

    ####################### Begin Project 4 work ###################

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        logging.debug(type(session))
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeSessionKey":
                setattr(sf, field.name, session.key.urlsafe())
        sf.check_initialized()
        return sf

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/sessions/{websafeConferenceKey}',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a websaveConferenceKey, return all sessions"""
        sessions = Session.query()
        sessions = sessions.filter(Session.webSafeConfId == request.websafeConferenceKey)

        # return set of SessionForm objects one per Session
        return SessionForms(items=[self._copySessionToForm(sn) for sn in sessions])

    @endpoints.method(SESSION_BY_TYPE, SessionForms,
            path='conference/{websafeConferenceKey}/sessions/{typeOfSession}',
            http_method='GET', name='getSessionsByType')
    def getSessionsByType(self, request):
        """Given a websaveConferenceKey, return all sessions of a specified type (eg lecture, keynote, workshop)"""
        sessions = Session.query()
        sessions = sessions.filter(Session.webSafeConfId == request.websafeConferenceKey)
        sessions = sessions.filter(Session.type == request.typeOfSession)

        # return set of SessionForm objects one per Session
        return SessionForms(items=[self._copySessionToForm(sn) for sn in sessions])

    @endpoints.method(SESSION_BY_SPEAKER, SessionForms,
            path='sessions/{speaker}',
            http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Given a speaker, return all sessions given by this particular speaker, across all conferences"""
        sessions = Session.query()
        sessions = sessions.filter(Session.speaker == request.speaker)

        # return set of SessionForm objects one per Session
        return SessionForms(items=[self._copySessionToForm(sesn) for sesn in sessions])

    @endpoints.method(SESSION_GET_REQUEST, SessionForm,
            path='session/{websafeConferenceKey}',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create or update Session object, returning SessionForm/request.
           Note: open only to the organizer of the conference"""
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # check for authorization, valid conference key, and that the current user is the conference orgainizer
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        try:
            conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        except TypeError:
            raise endpoints.BadRequestException('Sorry, only string is allowed as websafeConferenceKey input')
        except Exception, e:
            if e.__class__.__name__ == 'ProtocolBufferDecodeError':
                raise endpoints.BadRequestException('Sorry, the websafeConferenceKey string seems to be invalid')
            else:
                raise
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.webSafeConfId)
        if user_id != getattr(conf, 'organizerUserId'):
            raise endpoints.UnauthorizedException('Only conference organizer is authorized to add sessions.')

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey'] # session does not have a websafeConferenceKey

        # add default values for those missing (both data model & outbound Message)
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        # convert dates from strings to Date objects
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        data['webSafeConfId'] = request.websafeConferenceKey
        del data['websafeSessionKey'] # this is only in the SessionForm

        logging.debug(data)
        # creation of Session, record the key to get the item & return (modified) SessionForm
        sessionKey = Session(**data).put()
        # start the task to update the conference featured speaker if needed
        if data['speaker'] is not SESSION_DEFAULTS['speaker']:
            taskqueue.add(params={'websafeConferenceKey': request.websafeConferenceKey, 'speaker': data['speaker']},
                  url='/tasks/set_featured_speaker')

        return self._copySessionToForm(sessionKey.get())

    @endpoints.method(SESSION_ADD_WISH_REQUEST, BooleanMessage,
        path='sessions/addwish/{websafeSessionKey}',
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add a session (using the websaveSessionKey) to the users session wish list.
        Returns true if successful, false otherwise."""
        retval = None
        # make sure user is authorized
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # check if user already registered otherwise add
        # could also check to see if the session time conflicts with others already in the list
        if request.websafeSessionKey in profile.sessionKeysWishList:
            raise ConflictException(
                "You have already added this session to your wish list.")
        else:
            profile.sessionKeysWishList.append(request.websafeSessionKey)
            retval = True

        profile.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='sessions/wishlist',
        http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Return list of Sessions the user has in there wish list."""
        # make sure user is authorized
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # look up the sessions based on SessionKey in the profile.sessionKeysWishList
        s_keys = [ndb.Key(urlsafe=wsk) for wsk in profile.sessionKeysWishList]
        sessions = ndb.get_multi(s_keys)
        # return set of SessionForm objects one per Session
        return SessionForms(items=[self._copySessionToForm(sesn) for sesn in sessions])

    @endpoints.method(CONF_BY_TOPIC, ConferenceForms,
            path='getconferencebytopic/{topic}',
            http_method='GET', name='getConferenceByTopic')
    def getConferenceByTopic(self, request):
        """Given a speaker, return all sessions given by this particular speaker, across all conferences"""
        conferences = Conference.query()
        conferences = conferences.filter(Conference.topics.IN([request.topic]))
        # return individual ConferenceForm object per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, "") for conf in conferences])

    @endpoints.method(SESSION_BY_CITY, SessionForms,
            path='getsessionbycity/{city}',
            http_method='GET', name='getSessionByCity')
    def getSessionByCity(self, request):
        """Given a city, return all sessions across all conferences in the city."""
        # get all the conferences in the city
        c = Conference.query()
        c = c.filter(Conference.city == request.city)
        conferences = c.fetch()

        logging.debug(conferences)
        # get all the websafeConferenceKeys for the conferences
        confWebKeys = [conf.key.urlsafe() for conf in conferences]

        logging.debug(confWebKeys)
        # find all the sessions matching any of the conferences that matched the city, across all conferences
        sessions = Session.query()
        sessions = sessions.filter(Session.webSafeConfId.IN(confWebKeys))

        # return set of SessionForm objects one per Session
        return SessionForms(items=[self._copySessionToForm(sesn) for sesn in sessions])

    @staticmethod
    def _cacheFeaturedSpeaker(wsck, speaker):
        """Create the featured speaker announcement & assign to memcache.
        used by getFeaturedSpeaker().
        The announcement will have the following format:
        'Featured Speaker: <speaker>, Sessions: <session1>, <session2>, ...
        """
        sessions = Session.query()
        sessions = sessions.filter(Session.webSafeConfId == wsck)
        sessions = sessions.filter(Session.speaker == speaker)
        sessions = sessions.fetch()
        logging.debug(speaker)
        if(len(sessions) < 2):
            return
        announcement = "Featured speaker: %s, Sessions: " % speaker
        for session in sessions:
            announcement += "%s, " % session.name
        # might want to check that the websafeConferenceKey is not none
        # slice off the trailing ", "
        memcache.set(wsck, announcement[:-2])
        logging.debug(announcement)
        return announcement

    @endpoints.method(CONF_GET_REQUEST, StringMessage,
            path='conference/{websafeConferenceKey}/featuredspeaker/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return featured speaker for the conference from memcache (if there is one, '' if none)."""
        logging.debug(request.websafeConferenceKey)
        info = memcache.get(request.websafeConferenceKey)
        logging.debug(info)
        if not info:
            info = ''
        return StringMessage(data=info)

# registers API
api = endpoints.api_server([ConferenceApi]) 
