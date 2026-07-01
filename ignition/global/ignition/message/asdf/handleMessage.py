def handleMessage(payload):
	slowrm.drop_all([shirt.Shirt], "local")
	slowrm.sync_schema([shirt.Shirt], "local")
	
	with slowrm.Session("local") as session:
	    asdf = shirt.Shirt(color=shirt.Color.navy.value, size=shirt.Size.large.value)
	    session.add(asdf)
	    session.commit()
	
	with slowrm.Session("local") as session:
	    asdf = session.get(shirt.Shirt, 1)
	    print(shirt.Color(asdf.color))   # Color.navy
	    print(shirt.Size(asdf.size))     # Size.large