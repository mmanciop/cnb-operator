package com.canonical.java.test.mongodb;

import org.springframework.data.annotation.Id;

public class Thing {

    @Id
	public String id;

    public String name;

	public Thing() {}

	public Thing(String name) {
		this(null, name);
	}

    public Thing(String id, String name) {
		this.id = id;
		this.name = name;
	}

	@Override
	public String toString() {
		return String.format("Thing[id=%s, name='%s']", id, name);
	}

	@Override
	public int hashCode() {
		final int prime = 31;
		int result = 1;
		result = prime * result + ((name == null) ? 0 : name.hashCode());
		return result;
	}

	@Override
	public boolean equals(Object obj) {
		if (this == obj)
			return true;
		if (obj == null)
			return false;
		if (getClass() != obj.getClass())
			return false;
		Thing other = (Thing) obj;
		if (name == null) {
			if (other.name != null)
				return false;
		} else if (!name.equals(other.name))
			return false;
		return true;
	}

}
