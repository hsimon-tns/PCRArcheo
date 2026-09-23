UI.threads
==========

Thin wrapper around ``QThreadPool``/``QRunnable`` used to run network
requests off the GUI thread ; results must be reported back through
``Node``'s signals rather than called directly, since Qt automatically
marshals a cross-thread signal emission onto the receiver's thread.

.. automodule:: UI.threads
   :members:
   :undoc-members:
   :show-inheritance:
