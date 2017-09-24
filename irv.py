from functools import wraps
from flask import Flask, request, render_template, Response, jsonify, url_for
from flask_mail import Mail, Message
from sqlalchemy import create_engine, Table, Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.orderinglist import ordering_list
from uuid import uuid4

app = Flask(__name__)
app.config.from_pyfile('irv.cfg')

mail = Mail(app)

class InvalidUsage(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv

@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

engine = create_engine(app.config['DATABASE'], echo=True)
Session = sessionmaker(bind=engine)
session = Session()

Base = declarative_base()

class Election(Base):
    __tablename__ = 'elections'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    key = Column(String)

    positions = relationship('Position', order_by='Position.rank',
                                collection_class=ordering_list('rank'),
                                back_populates='election')
    voters = relationship('Voter', back_populates='election')

    def __str__(self):
        return self.name


class Position(Base):
    __tablename__ = 'positions'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    rank = Column(Integer)
    election_id = Column(Integer, ForeignKey('elections.id'))

    election = relationship('Election', back_populates='positions')
    candidates = relationship('Candidate', back_populates='position')

    def __str__(self):
        return self.name

class Candidate(Base):
    __tablename__ = 'candidates'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    position_id = Column(Integer, ForeignKey('positions.id'))

    position = relationship('Position', back_populates='candidates')
    votes = relationship('Vote', back_populates='candidate')

    def __str__(self):
        return self.name

class Vote(Base):
    __tablename__ = 'votes'
    __table_args__ = (
            UniqueConstraint('voter_id', 'candidate_id', name='unique_rank_candidate'),
            UniqueConstraint('rank', 'candidate_id', name='unique_rank_candidate'),
            )

    id = Column(Integer, primary_key=True)
    rank = Column(Integer)
    candidate_id = Column(Integer, ForeignKey('candidates.id'))
    voter_id = Column(Integer, ForeignKey('voters.id'))

    candidate = relationship('Candidate', back_populates='votes')
    voter = relationship('Voter', back_populates='votes')

class Voter(Base):
    __tablename__ = 'voters'

    id = Column(Integer, primary_key=True)
    key = Column(String)
    election_id = Column(Integer, ForeignKey('elections.id'))

    election = relationship('Election', back_populates='voters')
    votes = relationship('Vote', back_populates='voter')

Base.metadata.create_all(engine)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/election', methods = ['GET', 'POST'])
def create_election():
    """Create an Election given a JSON description.

    Example JSON request:
    {
        "name": "test election",
        "admin_email": "admin@example.com",
        "voter_emails": [
            "user@example.com"
        ],
        "positions": [
            {
                "name": "test ballot",
                "candidates": [
                    "test candidate"
                ]
            }
        ]
    }
    """
    if request.method == 'GET':
        return render_template('create.html')

    position_rank = 0
    election = Election(name=request.json['name'])
    election.key = str(uuid4())
    for position_json in request.json['positions']:
        position = Position(name=position_json['name'])
        position.election = election
        position.rank = position_rank
        position_rank += 1
        for candidate_name in position_json['candidates']:
            candidate = Candidate(name=candidate_name)
            candidate.position = position
    session.add(election)
    session.commit()
    manage_url = url_for("manage_election", election_id=election.id,
            _external=True) + "#" + election.key
    mail.send(Message("Election Created",
        recipients=[request.json['admin_email']],
        body=manage_url))
    for email in request.json['voter_emails']:
        voter = Voter()
        voter.election = election
        voter.key = str(uuid4())
        voter_url = url_for("vote_election", election_id=election.id,
                _external=True) + "#" + voter.key
        mail.send(Message("Vote in New Election",
            recipients=[email],
            body=voter_url))
    session.add(election)
    session.commit()
    return jsonify({
        "redirect": manage_url
        })

@app.route('/election/<int:election_id>/manage', methods = ['GET', 'POST'])
def manage_election(election_id):
    if request.method == 'GET':
        return render_template('manage.html')
    return ''

@app.route('/election/<int:election_id>/vote', methods = ['GET', 'POST'])
def vote_election(election_id):
    if request.method == 'GET':
        positions = session.query(Position).\
                filter(Position.election_id == election_id).\
                all()
        return render_template('vote.html', positions=positions)

    voter = session.query(Voter).\
            filter(Voter.key == request.json['key']).\
            filter(Voter.election_id == election_id).\
            one()

    old_votes = session.query(Vote).\
            filter(Vote.voter==voter).\
            join(Vote.candidate).\
            join(Candidate.position).\
            filter(Position.election_id==election_id).\
            all()
    for old_vote in old_votes:
        session.delete(old_vote)
    session.commit()

    for ballot in request.json['votes']:
        position = session.query(Position).\
                filter(Position.id == ballot['position_id']).\
                one()
        rank = 1
        for candidate_id in ballot['candidate_ids']:
            vote = Vote(rank=rank, candidate_id=candidate_id, voter=voter)
            rank += 1

    session.add(voter)
    session.commit()

    return jsonify({
        "redirect": url_for("results_election", election_id=election_id, _external=True)
        })

@app.route('/election/<int:election_id>/results', methods = ['GET'])
def results_election(election_id):
    return render_template('results.html')

if __name__ == '__main__':
    app.run()
